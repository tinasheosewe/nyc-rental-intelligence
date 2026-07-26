"""
ManagementScorer — scores buildings by owner/management company reputation.

Uses PLUTO (``ds_pluto``) to identify the building's owner (``ownername``),
then queries HPD Complaints (``ds_hpd_complaints``) across **all** buildings
owned by that entity to produce a portfolio-level complaint rate, broken
down by category.

Also integrates:
- **311 HEAT/HOT WATER** complaints within 200 m (building-level).
- **HPD Litigations** — active lawsuits filed by HPD against the owner
  (looked up by BBL; indicates severe negligence).
- **Evictions** — executed residential evictions at this building (12 mo).

Scoring:
    recency-weighted complaints (HPD + 311 heat, half-life 180 days)
    + litigation penalty (×10 each) + eviction penalty (×5 each, decayed)
    turned into an exposure-aware empirical-Bayes rate per portfolio unit
    (``eb_rate``, k=10, prior = citywide baseline mean), then scored
    against the frozen citywide baseline (batch percentile fallback).
    Fewer complaints per unit → higher score.

    Exposure-aware EB replaces the old zero-is-perfect pin: a 3-unit
    portfolio with 0 complaints shrinks toward the citywide prior
    (mid-high score — thin evidence), while a 300-unit portfolio with 0
    complaints earns a top score (strong evidence).  One-cycle
    convergence: on the very first pass (before ``build_baseline.py``
    has run) ``baseline_median`` is None and ``eb_rate`` falls back to the
    raw rate; rates converge to the shrunk form on the next scoring pass
    after the baseline exists (cached blocks written pre-baseline keep
    raw rates until their cache entry is refreshed).

    Attribution guard: the nearest PLUTO lot is only accepted within
    ``BUILDING_MATCH_MAX_M`` (40 m; measured p95 match distance is 25 m).
    Beyond that the building-specific parts are unknown → score = None
    and ``mgmt_match_uncertain`` = 1.  Attribution is resolved per unique
    listing coordinate (lat/lon rounded to 5 dp — listings in the same
    building share coordinates; different buildings never do), NOT per
    geohash cell: cell-level (~150 m) dedupe let whichever listing hit a
    cell first "own" it, so every other building in the cell inherited a
    stranger's building record.  Building-specific stats (owner
    portfolio, litigations, evictions) are cached by BBL
    (``bbl:<bbl>``); the lot resolution itself is cached per coordinate
    (``lot:<lat5>,<lon5>``); only the area-level 311 heat part keeps
    geohash-cell caching.  A building built within ~3 years
    with zero owner/building records (portfolio complaints, litigations,
    evictions) has no track record yet → score = None and
    ``mgmt_new_building`` = 1 (not a fake perfect score).

Output columns:
    mgmt_owner              TEXT    — owner name from PLUTO
    mgmt_owner_buildings    INTEGER — buildings in portfolio
    mgmt_owner_units        INTEGER — residential units in portfolio
    mgmt_complaints         INTEGER — HPD complaints (12 mo), all categories
    mgmt_hpd_heat           INTEGER — HPD HEAT/HOT WATER complaints
    mgmt_hpd_plumbing       INTEGER — HPD PLUMBING complaints
    mgmt_hpd_paint          INTEGER — HPD PAINT/PLASTER complaints
    mgmt_hpd_safety         INTEGER — HPD SAFETY complaints
    mgmt_heat_complaints    INTEGER — 311 HEAT/HOT WATER (area-level, 200 m)
    mgmt_litigations        INTEGER — active HPD litigations for this BBL
    mgmt_evictions          INTEGER — residential evictions at building (12 mo)
    mgmt_complaints_weighted REAL  — decayed weighted complaint total
    mgmt_complaints_per_unit REAL  — EB-shrunk weighted rate per portfolio
                                     unit (None when unscoreable)
    mgmt_match_uncertain    INTEGER — 1 when no PLUTO lot within 40 m
    mgmt_new_building       INTEGER — 1 when built <~3 yr with zero records
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import (
    baseline_median,
    baseline_scores,
    decay_weight,
    eb_rate,
)
from apthunt.scoring.utils import (
    BUILDING_MATCH_MAX_M,
    dedupe_by_geohash,
    find_nearest_row,
    is_new_building,
    normalize_bbl,
    parse_bbl,
    percentile_scores,
)

# HPD major_category buckets we surface individually
_CATEGORY_KEYS = {
    "HEAT/HOT WATER":  "mgmt_hpd_heat",
    "PLUMBING":        "mgmt_hpd_plumbing",
    "PAINT/PLASTER":   "mgmt_hpd_paint",
    "SAFETY":          "mgmt_hpd_safety",
}

# Cache source (version-bumped: v5 cached full building stats under
# geohash-cell keys, which cross-attributed neighboring buildings).
# Three key shapes share this source and cannot collide:
#   "<geohash>"          — area-level 311 heat part (cell-scoped is correct)
#   "lot:<lat5>,<lon5>"  — resolved PLUTO lot per unique listing coordinate
#   "bbl:<bbl>"          — building/owner record keyed by the actual lot
_SOURCE = "management_v6"


class ManagementScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache
        self._owner_cache: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "management"

    # Citywide baseline declaration (sampled by scripts/build_baseline.py)
    baseline_component = "mgmt_complaints_per_unit"
    baseline_reverse = True
    # Exposure-aware EB replaces the zero-is-perfect pin: zero counts on a
    # tiny portfolio must NOT read as perfect (absence of evidence).
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "mgmt_owner": "TEXT",
            "mgmt_owner_buildings": "INTEGER",
            "mgmt_owner_units": "INTEGER",
            "mgmt_complaints": "INTEGER",
            "mgmt_hpd_heat": "INTEGER",
            "mgmt_hpd_plumbing": "INTEGER",
            "mgmt_hpd_paint": "INTEGER",
            "mgmt_hpd_safety": "INTEGER",
            "mgmt_heat_complaints": "INTEGER",
            "mgmt_litigations": "INTEGER",
            "mgmt_evictions": "INTEGER",
            "mgmt_complaints_weighted": "REAL",
            "mgmt_complaints_per_unit": "REAL",
            "mgmt_match_uncertain": "INTEGER",
            "mgmt_new_building": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("pluto", quiet=True)
        self._store.ensure_downloaded("hpd_complaints", quiet=True)
        self._store.ensure_downloaded("noise", quiet=True)
        self._store.ensure_downloaded("hpd_litigations", quiet=True)
        self._store.ensure_downloaded("evictions", quiet=True)

        HEAT_RADIUS_M = 200

        today_ord = datetime.now().toordinal()

        # EB prior: citywide mean of the raw per-unit rate.  None until the
        # first build_baseline.py run — eb_rate then falls back to the raw
        # rate, so rates converge to the shrunk form one cycle later.
        prior = baseline_median(conn, self.name)

        # ---- Area-level part: 311 heat within 200 m.  A radius query
        # around the cell centroid, not a building attribute — geohash-cell
        # dedupe/caching stays correct here.
        gh_map = dedupe_by_geohash(listings)
        area_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, _SOURCE)
            if cached is None:
                heat_311, heat_weighted = self._heat_311_count(
                    lat, lon, HEAT_RADIUS_M, today_ord,
                )
                cached = {
                    "mgmt_heat_complaints": heat_311,
                    "_heat_weighted": heat_weighted,
                }
                self._cache.put(gh, _SOURCE, cached)
            area_stats[gh] = cached

        # ---- Building-level part: resolved per unique LISTING COORDINATE,
        # never per geohash cell.  Listings in the same building share
        # (lat, lon); different buildings never do — so rounded coordinate
        # pairs are the correct dedupe unit, and the resulting record is
        # cached under the resolved lot's BBL ("bbl:<bbl>") so a stranger's
        # building can never own a whole ~150 m cell again.
        coord_map: dict[tuple, tuple[float, float]] = {}
        for lst in listings:
            ck = (round(lst["lat"], 5), round(lst["lon"], 5))
            coord_map.setdefault(ck, (lst["lat"], lst["lon"]))

        bldg_stats: dict[tuple, dict] = {}
        for ck, (lat, lon) in coord_map.items():
            lot = self._resolve_lot(lat, lon, ck)
            bldg_stats[ck] = (
                None if lot is None
                else self._building_record(lot, today_ord)
            )

        # ---- Per-listing assembly: building part (BBL-keyed) + area part
        # (cell-keyed) may come from different cache keys.
        listing_stats: list[dict] = []
        for lst in listings:
            ck = (round(lst["lat"], 5), round(lst["lon"], 5))
            listing_stats.append(
                self._assemble(
                    bldg_stats[ck], area_stats[lst["geohash"]], prior,
                )
            )

        raw_values = [s["mgmt_complaints_per_unit"] for s in listing_stats]
        pct_scores = baseline_scores(
            conn, self.name, raw_values, reverse=True, zero_is_perfect=False,
        )
        if pct_scores is None:
            # Fallback until the first citywide baseline build
            # (None raw values stay None: unscoreable blocks get no score)
            known = [v for v in raw_values if v is not None]
            known_iter = iter(percentile_scores(known, reverse=True))
            pct_scores = [
                next(known_iter) if v is not None else None
                for v in raw_values
            ]

        results: list[ScorerResult] = []
        for lst, stats, pct in zip(listings, listing_stats, pct_scores):
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=pct,
                    components={k: v for k, v in stats.items()},
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_lot(self, lat: float, lon: float, ck: tuple) -> dict | None:
        """Resolve the nearest PLUTO lot for one listing coordinate.

        Cached per rounded coordinate pair under ``lot:<lat5>,<lon5>`` so
        repeated runs skip the bbox query.  Returns a slim lot dict
        (bbl / ownername / yearbuilt / _dist_m) or None when no lot lies
        within ``BUILDING_MATCH_MAX_M`` (40 m attribution guard).
        """
        key = f"lot:{ck[0]},{ck[1]}"
        cached = self._cache.get(key, _SOURCE)
        if cached is not None:
            return cached if cached.get("matched") else None

        pluto_rows = self._store.query_bbox("pluto", lat, lon, delta=0.0015)
        # Attribution guard: only accept a lot within 40 m — beyond
        # that the match is likely a neighbor's building.
        nearest = find_nearest_row(
            pluto_rows, lat, lon, max_dist_m=BUILDING_MATCH_MAX_M,
        )
        if nearest is None:
            self._cache.put(key, _SOURCE, {"matched": False})
            return None
        slim = {
            "matched": True,
            "bbl": nearest.get("bbl"),
            "ownername": nearest.get("ownername"),
            "yearbuilt": nearest.get("yearbuilt"),
            "_dist_m": nearest.get("_dist_m"),
        }
        self._cache.put(key, _SOURCE, slim)
        return slim

    def _building_record(self, lot: dict, today_ord: int) -> dict:
        """Building-specific record for a *matched* lot, cached by BBL.

        Contains the owner-portfolio stats plus this building's
        litigation/eviction counts and the pre-flag new-building marker.
        The combined weighted rate and flags are assembled per listing.
        """
        bbl = normalize_bbl(lot["bbl"]) if lot.get("bbl") else None
        key = f"bbl:{bbl}" if bbl else None
        if key is not None:
            cached = self._cache.get(key, _SOURCE)
            if cached is not None:
                return cached

        evictions, evict_weighted = self._eviction_count(lot, today_ord)
        litigations = self._litigation_count(lot)

        if not lot.get("ownername"):
            record = self._empty_stats()
        else:
            record = self._get_owner_stats(lot["ownername"].strip(), today_ord)

        record["mgmt_litigations"] = litigations
        record["mgmt_evictions"] = evictions
        record["_evict_weighted"] = evict_weighted
        # Building/owner track record only (311 heat is area-level and
        # can come from neighbors, so it doesn't count as a record).
        record["_new_building"] = int(
            is_new_building(lot)
            and record["mgmt_complaints"] == 0
            and litigations == 0
            and evictions == 0
        )
        if key is not None:
            self._cache.put(key, _SOURCE, record)
        return record

    def _assemble(
        self, bldg: dict | None, area: dict, prior,
    ) -> dict:
        """Combine one listing's building record and area record into the
        final component dict (building and area parts may come from
        different cache keys)."""
        match_uncertain = bldg is None
        if match_uncertain:
            stats = self._empty_stats()
            stats["mgmt_litigations"] = 0
            stats["mgmt_evictions"] = 0
        else:
            stats = dict(bldg)  # copy: cached/shared record must not mutate

        # Combine portfolio HPD weight with per-building signals.
        # Litigations are *active* lawsuits (undated status) — no decay.
        hpd_weighted = stats.pop("_hpd_weighted")
        evict_weighted = stats.pop("_evict_weighted", 0.0)
        new_building = bool(stats.pop("_new_building", 0))
        litigations = stats["mgmt_litigations"]
        heat_weighted = area.get("_heat_weighted", 0.0)

        stats["mgmt_heat_complaints"] = area["mgmt_heat_complaints"]
        weighted_total = (
            hpd_weighted + heat_weighted
            + litigations * 10 + evict_weighted * 5
        )
        stats["mgmt_complaints_weighted"] = round(weighted_total, 3)
        stats["mgmt_match_uncertain"] = int(match_uncertain)
        stats["mgmt_new_building"] = int(new_building)

        if match_uncertain or new_building:
            # Building attribution failed, or a brand-new building with
            # no track record: the rate is unknown, not perfect.
            stats["mgmt_complaints_per_unit"] = None
        else:
            # Exposure-aware EB rate per portfolio unit: zero counts on
            # a large portfolio → near-zero rate (strong evidence);
            # zero counts on a tiny portfolio → shrinks to the prior.
            stats["mgmt_complaints_per_unit"] = round(
                eb_rate(
                    weighted_total,
                    stats["mgmt_owner_units"],
                    prior,
                    k=10.0,
                ),
                4,
            )
        return stats

    @staticmethod
    def _empty_stats() -> dict:
        return {
            "mgmt_owner": "",
            "mgmt_owner_buildings": 0,
            "mgmt_owner_units": 0,
            "mgmt_complaints": 0,
            "mgmt_hpd_heat": 0,
            "mgmt_hpd_plumbing": 0,
            "mgmt_hpd_paint": 0,
            "mgmt_hpd_safety": 0,
            "_hpd_weighted": 0.0,
        }

    def _heat_311_count(
        self, lat: float, lon: float, radius_m: int, today_ord: int,
    ):
        """Return (count, kernel×decay weighted sum) of 311 heat complaints."""
        rows = self._store.query_circle(
            "noise", lat=lat, lon=lon, radius_m=radius_m,
            select="complaint_type,created_date",
        )
        sigma = radius_m / 2.0
        count = 0
        weighted = 0.0
        for r in rows:
            if r.get("complaint_type") != "HEAT/HOT WATER":
                continue
            count += 1
            kernel = math.exp(-(float(r.get("_dist_m", 0.0)) / sigma) ** 2)
            weighted += kernel * decay_weight(r.get("created_date"), today_ord)
        return count, weighted

    def _eviction_count(self, nearest_lot: dict | None, today_ord: int):
        """Count evictions at this building by BBL (plus decayed weight)."""
        if nearest_lot is None:
            return 0, 0.0
        raw_bbl = nearest_lot.get("bbl")
        if not raw_bbl:
            return 0, 0.0
        bbl_str = normalize_bbl(raw_bbl)
        rows = self._store.query(
            "evictions",
            where_clause="bbl = ?",
            params=(bbl_str,),
            select="executed_date",
        )
        weighted = sum(
            decay_weight(r.get("executed_date"), today_ord) for r in rows
        )
        return len(rows), weighted

    def _litigation_count(self, nearest_lot: dict | None) -> int:
        if nearest_lot is None:
            return 0
        raw_bbl = nearest_lot.get("bbl")
        if not raw_bbl:
            return 0
        try:
            boro, block, lot = parse_bbl(raw_bbl)
        except (ValueError, IndexError):
            return 0
        rows = self._store.query(
            "hpd_litigations",
            where_clause="boroid = ? AND block = ? AND lot = ?",
            params=(boro, str(int(block)), str(int(lot))),
            select="COUNT(*) as cnt",
        )
        return int(rows[0]["cnt"]) if rows else 0

    def _get_owner_stats(self, owner: str, today_ord: int) -> dict:
        """Portfolio-level stats for an owner (per-building fields and the
        combined weighted rate are layered on by the caller)."""
        if owner in self._owner_cache:
            return dict(self._owner_cache[owner])

        bbls = self._store.query(
            "pluto",
            where_clause="ownername = ?",
            params=(owner,),
            select="bbl, unitsres",
        )

        total_units = 0
        total_buildings = len(bbls)
        bbl_set = set()

        for row in bbls:
            bbl = row.get("bbl")
            if bbl:
                bbl_set.add(normalize_bbl(bbl))
            try:
                total_units += int(float(row.get("unitsres") or 0))
            except (ValueError, TypeError):
                pass

        # Count HPD complaints by category across portfolio
        # (plain counts for the UI/flags, recency-decayed sum for scoring)
        total_complaints = 0
        hpd_weighted = 0.0
        cat_counts = {v: 0 for v in _CATEGORY_KEYS.values()}

        bbl_list = list(bbl_set)
        for i in range(0, len(bbl_list), 50):
            batch = bbl_list[i:i + 50]
            placeholders = ",".join("?" * len(batch))
            rows = self._store.query(
                "hpd_complaints",
                where_clause=f"bbl IN ({placeholders})",
                params=tuple(batch),
                select="major_category,received_date",
            )
            for r in rows:
                total_complaints += 1
                hpd_weighted += decay_weight(r.get("received_date"), today_ord)
                col = _CATEGORY_KEYS.get(r.get("major_category", ""))
                if col:
                    cat_counts[col] += 1

        stats = {
            "mgmt_owner": owner,
            "mgmt_owner_buildings": total_buildings,
            "mgmt_owner_units": total_units,
            "mgmt_complaints": total_complaints,
            **cat_counts,
            "_hpd_weighted": round(hpd_weighted, 3),
        }
        self._owner_cache[owner] = stats
        return dict(stats)


