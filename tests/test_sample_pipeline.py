"""
Sample data end to end: generator -> sync -> scoring -> API.

Everything here runs offline. Only the scorers that need no open-data
downloads (deal, unit amenities) are exercised; the geo scorers are
covered by scripts/validate_scores.py against a fully loaded database.
"""

from __future__ import annotations

import json
import sqlite3
import unittest

from apthunt.db import DB_PATH, get_connection
from apthunt.ingest import (
    CANONICAL_FIELDS,
    REQUIRED_FIELDS,
    ListingSource,
    available_sources,
    get_source,
    init_schema,
    sync,
)
from apthunt.ingest.sample import SampleSource
from apthunt.scoring.deal import DealScorer
from apthunt.scoring.engine import ScoringEngine
from apthunt.scoring.unit_amenities import UnitAmenitiesScorer

AS_OF = "2026-07-01"
T1 = "2026-07-01T12:00:00+00:00"
T2 = "2026-07-02T12:00:00+00:00"

# The heatmap grid's bounding box — every sample listing must land in NYC.
NYC = {"lat": (40.49, 40.92), "lon": (-74.27, -73.68)}


def memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


class FixedSource(ListingSource):
    """A source that yields exactly what it is given."""

    name = "fixed"

    def __init__(self, listings):
        self._listings = listings

    def fetch(self):
        return iter(self._listings)


def minimal(source_id: str, **extra) -> dict:
    return {"source_id": source_id, "price": 3000, "beds": 1,
            "lat": 40.72, "lon": -73.95, **extra}


class SampleSourceTest(unittest.TestCase):

    def test_is_the_registered_default(self):
        self.assertIn("sample", available_sources())
        self.assertIsInstance(get_source("sample", as_of=AS_OF), SampleSource)

    def test_same_seed_and_date_give_identical_listings(self):
        first = list(SampleSource(as_of=AS_OF).fetch())
        second = list(SampleSource(as_of=AS_OF).fetch())
        self.assertEqual(first, second)

    def test_seed_changes_the_listings(self):
        self.assertNotEqual(
            list(SampleSource(seed=1, as_of=AS_OF).fetch()),
            list(SampleSource(seed=2, as_of=AS_OF).fetch()),
        )

    def test_as_of_moves_dates_but_nothing_else(self):
        dated = {"first_seen_at", "available_at", "price_history", "raw_json"}
        july = list(SampleSource(as_of="2026-07-01").fetch())
        august = list(SampleSource(as_of="2026-08-01").fetch())
        for a, b in zip(july, august):
            self.assertEqual(
                {k: v for k, v in a.items() if k not in dated},
                {k: v for k, v in b.items() if k not in dated},
            )
        self.assertNotEqual(july[0]["first_seen_at"], august[0]["first_seen_at"])

    def test_shape(self):
        listings = list(SampleSource(as_of=AS_OF).fetch())
        self.assertEqual(len(listings), 300)
        self.assertEqual(len({l["source_id"] for l in listings}), 300)
        self.assertEqual(
            len({(l["address"], l["unit"]) for l in listings}), 300,
            "two listings share an address + unit",
        )
        for l in listings:
            self.assertLessEqual(set(l), set(CANONICAL_FIELDS) | {"first_seen_at"})
            for field in REQUIRED_FIELDS:
                self.assertIsNotNone(l[field], field)
            self.assertTrue(NYC["lat"][0] < l["lat"] < NYC["lat"][1])
            self.assertTrue(NYC["lon"][0] < l["lon"] < NYC["lon"][1])
            self.assertGreater(l["price"], 1000)
            if l["net_effective_price"] is not None:
                self.assertLess(l["net_effective_price"], l["price"])

    def test_every_listing_says_it_is_synthetic(self):
        for l in SampleSource(as_of=AS_OF).fetch():
            self.assertIn("Synthetic sample listing", l["description"])
            self.assertIs(l["raw_json"]["synthetic"], True)
            self.assertIsNone(l["url"])
            self.assertIsNone(l["photos"])


class SyncTest(unittest.TestCase):

    def test_schema_has_a_column_for_every_api_dimension(self):
        from api.composite import SCORE_KEYS

        conn = memory_db()
        init_schema(conn)
        init_schema(conn)  # idempotent
        columns = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
        self.assertLessEqual({f"{key}_score" for key in SCORE_KEYS}, columns)

    def test_refresh_keeps_identity_and_first_seen(self):
        conn = memory_db()
        first = sync(conn, SampleSource(as_of=AS_OF), now=T1)
        before = {r["source_id"]: (r["id"], r["first_seen_at"])
                  for r in conn.execute("SELECT * FROM listings")}
        second = sync(conn, SampleSource(as_of=AS_OF), now=T2)

        self.assertEqual(first, {"seen": 300, "inserted": 300, "updated": 0, "deactivated": 0})
        self.assertEqual(second, {"seen": 300, "inserted": 0, "updated": 300, "deactivated": 0})
        after = {r["source_id"]: (r["id"], r["first_seen_at"])
                 for r in conn.execute("SELECT * FROM listings")}
        self.assertEqual(before, after)
        self.assertEqual(
            {r[0] for r in conn.execute("SELECT DISTINCT last_seen_at FROM listings")}, {T2}
        )
        self.assertEqual(
            [r["status"] for r in conn.execute("SELECT status FROM sync_log ORDER BY id")],
            ["success", "success"],
        )

    def test_first_seen_runs_on_our_clock_unless_backfilled(self):
        conn = memory_db()
        sync(conn, FixedSource([
            minimal("a"),
            minimal("b", first_seen_at="2026-05-01T00:00:00+00:00"),
        ]), now=T1)
        seen = dict(conn.execute("SELECT source_id, first_seen_at FROM listings"))
        self.assertEqual(seen, {"a": T1, "b": "2026-05-01T00:00:00+00:00"})

    def test_listings_a_source_stops_reporting_go_inactive(self):
        conn = memory_db()
        sync(conn, FixedSource([minimal("a"), minimal("b")]), now=T1)
        stats = sync(conn, FixedSource([minimal("a", price=2900)]), now=T2)
        self.assertEqual(stats["deactivated"], 1)
        rows = {r["source_id"]: r for r in conn.execute("SELECT * FROM listings")}
        self.assertEqual(rows["a"]["status"], "ACTIVE")
        self.assertEqual(rows["a"]["price"], 2900)
        self.assertEqual(rows["b"]["status"], "INACTIVE")
        self.assertEqual(rows["b"]["last_seen_at"], T1)

        sync(conn, FixedSource([minimal("a"), minimal("b")]), now=T2)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM listings WHERE status = 'ACTIVE'").fetchone()[0], 2
        )

    def test_refresh_does_not_blank_scorer_filled_building_facts(self):
        conn = memory_db()
        sync(conn, FixedSource([minimal("a")]), now=T1)
        conn.execute("UPDATE listings SET building_year = 1931")  # as a scorer would
        sync(conn, FixedSource([minimal("a", price=3100)]), now=T2)
        row = conn.execute("SELECT price, building_year FROM listings").fetchone()
        self.assertEqual((row["price"], row["building_year"]), (3100, 1931))

    def test_sources_do_not_touch_each_others_listings(self):
        conn = memory_db()
        sync(conn, SampleSource(count=30, as_of=AS_OF), now=T1)
        sync(conn, FixedSource([minimal("a")]), now=T2)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM listings WHERE source = 'sample' AND status = 'ACTIVE'"
            ).fetchone()[0],
            30,
        )

    def test_bad_listings_fail_the_whole_sync(self):
        for bad in (
            minimal("x", bedrooms=2),          # not a canonical field
            {"source_id": "x", "price": 3000},  # required fields missing
        ):
            conn = memory_db()
            with self.assertRaises(ValueError):
                sync(conn, FixedSource([minimal("ok"), bad]), now=T1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0], 0)
            self.assertEqual(
                conn.execute("SELECT status FROM sync_log").fetchone()[0], "error"
            )


class GroupSortTest(unittest.TestCase):
    """Sorting the feed by a group must follow the group score the API reports."""

    def test_sql_group_score_matches_compute_group_scores(self):
        import random

        from api.composite import SCORE_GROUPS, SCORE_KEYS, compute_group_scores
        from api.routers.listings import _SORT_MAP

        conn = memory_db()
        sync(conn, FixedSource([minimal(f"l{i}") for i in range(60)]), now=T1)

        rng = random.Random(7)
        scores_by_id = {}
        for (listing_id,) in conn.execute("SELECT id FROM listings").fetchall():
            scores = {
                key: None if rng.random() < 0.35 else round(rng.uniform(0, 100), 1)
                for key in SCORE_KEYS
            }
            conn.execute(
                "UPDATE listings SET "
                + ", ".join(f"{key}_score = ?" for key in SCORE_KEYS)
                + " WHERE id = ?",
                [scores[key] for key in SCORE_KEYS] + [listing_id],
            )
            scores_by_id[listing_id] = scores

        cases = [(group, group, False) for group in SCORE_GROUPS]
        cases.append(("neighborhood_no_schools", "neighborhood", True))
        unscored = 0
        for sort_key, group, exclude_schools in cases:
            for listing_id, value in conn.execute(
                f"SELECT id, {_SORT_MAP[sort_key]} FROM listings"
            ):
                expected = compute_group_scores(
                    scores_by_id[listing_id], exclude_schools=exclude_schools
                )[group]
                if expected is None:
                    unscored += 1
                    self.assertIsNone(value, sort_key)
                else:
                    self.assertAlmostEqual(value, expected, places=6, msg=sort_key)
        self.assertGreater(unscored, 0, "no listing exercised the all-NULL case")

    def test_every_dimension_is_sortable(self):
        from api.composite import SCORE_KEYS
        from api.routers.listings import _SORT_MAP

        self.assertLessEqual(set(SCORE_KEYS), set(_SORT_MAP))


class PipelineTest(unittest.TestCase):
    """Sample listings, scored offline, served by the API."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        conn = get_connection(DB_PATH)
        sync(conn, SampleSource(as_of=AS_OF), now=T1)
        engine = ScoringEngine(conn)
        engine.register(DealScorer()).register(UnitAmenitiesScorer())
        cls.stats = engine.run()
        cls.rows = [dict(r) for r in conn.execute("SELECT * FROM listings")]
        conn.close()

        from api.app import app

        cls._client_ctx = TestClient(app)
        cls.client = cls._client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._client_ctx.__exit__(None, None, None)

    def test_deal_scorer_finds_comps_for_most_listings(self):
        scored = [r for r in self.rows if r["deal_score"] is not None]
        self.assertGreater(len(scored), 0.9 * len(self.rows))
        self.assertTrue(all(0 <= r["deal_score"] <= 100 for r in scored))
        self.assertTrue(all(r["comp_set_size"] >= 3 for r in scored))
        # leverage + tenure signals are fed by the sample's history fields
        self.assertTrue(any(r["deal_price_cut_pct"] for r in scored))
        self.assertTrue(any(r["deal_months_free"] for r in scored))
        self.assertTrue(any(r["tenure_median_months"] for r in scored))

    def test_unit_amenities_scored_only_where_amenities_exist(self):
        for r in self.rows:
            has_amenities = bool(r["amenities"] and json.loads(r["amenities"]))
            self.assertEqual(r["unit_amenities_score"] is not None, has_amenities)

    def test_health(self):
        body = self.client.get("/api/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"]["api_visible_listings"], 300)

    def test_feed(self):
        body = self.client.get("/api/listings", params={"page_size": 5}).json()
        self.assertEqual(body["total"], 300)
        self.assertEqual(len(body["listings"]), 5)
        composites = [l["scores"]["composite"] for l in body["listings"]]
        self.assertEqual(composites, sorted(composites, reverse=True))

    def test_feed_filters(self):
        body = self.client.get("/api/listings", params={
            "beds": "1,2", "max_price": 3200, "neighborhoods": "Astoria,Harlem",
            "page_size": 200,
        }).json()
        self.assertGreater(body["total"], 0)
        for l in body["listings"]:
            self.assertIn(l["beds"], (1, 2))
            self.assertLessEqual(l["price"], 3200)
            self.assertIn(l["neighborhood"], ("Astoria", "Harlem"))

    def test_sorting_by_a_dimension_nobody_scored_yet_is_not_an_error(self):
        for sort in ("deal", "crime", "transit", "building", "safety"):
            resp = self.client.get("/api/listings", params={"sort": sort, "page_size": 3})
            self.assertEqual(resp.status_code, 200, sort)
        resp = self.client.get("/api/listings", params={"min_data_quality": "limited"})
        self.assertEqual(resp.status_code, 200)

    def test_detail(self):
        best = self.client.get(
            "/api/listings", params={"sort": "deal", "page_size": 1}
        ).json()["listings"][0]
        detail = self.client.get(f"/api/listings/{best['id']}").json()
        self.assertEqual(detail["id"], best["id"])
        self.assertEqual(detail["scores"]["deal"], best["scores"]["deal"])
        self.assertIn("deal", detail["score_explanations"])
        self.assertTrue(detail["similar"])
        self.assertTrue(detail["neighborhood_info"]["description"])
        self.assertIn("Synthetic sample listing", detail["description"])
        self.assertEqual(self.client.get("/api/listings/does-not-exist").status_code, 404)

    def test_lookups(self):
        self.assertEqual(len(self.client.get("/api/neighborhoods").json()), 26)
        self.assertIn("Dishwasher", self.client.get("/api/amenities").json())


if __name__ == "__main__":
    unittest.main()
