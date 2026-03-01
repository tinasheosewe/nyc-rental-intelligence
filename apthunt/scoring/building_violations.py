"""
BuildingViolationsScorer — scores listings by active building violations.

Combines two violation sources:

1. **DOB Violations** (``ds_dob_violations``) — Department of Buildings
   violations for code/construction issues.  Looked up by BBL.

2. **HPD Violations** (``ds_hpd_violations``) — Housing Preservation &
   Development violations for habitability issues.  Inspector-confirmed,
   classified by severity:
     Class A = non-hazardous (90 days to fix)
     Class B = hazardous (30 days to fix)
     Class C = **immediately hazardous** (24 hours — lead, no heat, gas …)

3. **DOB Active Permits** (``ds_dob_permits``) — active construction
   permits on the building or nearby.  Surfaced as a breakout component
   (not scored — informational only).

Scoring:
    Weighted violation rate per unit:
        weighted = DOB + HPD-A×1 + HPD-B×2 + HPD-C×5
    Percentile-ranked.  Fewer violations per unit → higher score.
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import (
    dedupe_by_geohash,
    find_nearest_row,
    parse_bbl,
    percentile_scores,
    pluto_units,
)


class BuildingViolationsScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "building_violations"

    def columns(self) -> dict[str, str]:
        return {
            "building_violation_count": "INTEGER",
            "building_hpd_class_a": "INTEGER",
            "building_hpd_class_b": "INTEGER",
            "building_hpd_class_c": "INTEGER",
            "building_unitsres": "INTEGER",
            "building_violations_per_unit": "REAL",
            "building_active_permits": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("pluto", quiet=True)
        self._store.ensure_downloaded("dob_violations", quiet=True)
        self._store.ensure_downloaded("hpd_violations", quiet=True)
        self._store.ensure_downloaded("dob_permits", quiet=True)

        gh_map = dedupe_by_geohash(listings)

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "bv_v2")
            if cached is not None:
                block_stats[gh] = cached
                continue

            pluto_rows = self._cache.get_or_fetch(
                gh,
                "pluto",
                lambda lat=lat, lon=lon: self._store.query_bbox(
                    "pluto", lat, lon, delta=0.0015,
                ),
            )
            nearest = find_nearest_row(pluto_rows, lat, lon)

            if nearest is None:
                stats = {
                    "building_violation_count": 0,
                    "building_hpd_class_a": 0,
                    "building_hpd_class_b": 0,
                    "building_hpd_class_c": 0,
                    "building_unitsres": 1,
                    "building_active_permits": 0,
                }
                block_stats[gh] = stats
                self._cache.put(gh, "bv_v2", stats)
                continue

            bbl_raw = nearest.get("bbl", "")
            unitsres = pluto_units(nearest)

            try:
                boro, block, lot = parse_bbl(bbl_raw)
            except (ValueError, IndexError):
                stats = {
                    "building_violation_count": 0,
                    "building_hpd_class_a": 0,
                    "building_hpd_class_b": 0,
                    "building_hpd_class_c": 0,
                    "building_unitsres": unitsres,
                    "building_active_permits": 0,
                }
                block_stats[gh] = stats
                self._cache.put(gh, "bv_v2", stats)
                continue

            # DOB violations
            dob_count = len(self._fetch_dob_violations(boro, block, lot))

            # HPD violations by class
            hpd_a, hpd_b, hpd_c = self._fetch_hpd_violations(boro, block, lot)

            # DOB active permits
            permits = self._fetch_permits(boro, block, lot)

            stats = {
                "building_violation_count": dob_count,
                "building_hpd_class_a": hpd_a,
                "building_hpd_class_b": hpd_b,
                "building_hpd_class_c": hpd_c,
                "building_unitsres": unitsres,
                "building_active_permits": permits,
            }
            block_stats[gh] = stats
            self._cache.put(gh, "bv_v2", stats)

        # Weighted violation rate per unit for scoring
        per_units: list[float] = []
        for lst in listings:
            s = block_stats[lst["geohash"]]
            weighted = (
                s["building_violation_count"]
                + s["building_hpd_class_a"]
                + s["building_hpd_class_b"] * 2
                + s["building_hpd_class_c"] * 5
            )
            per_units.append(weighted / max(1, s["building_unitsres"]))

        scores = percentile_scores(per_units, reverse=True, zero_is_perfect=True)

        results: list[ScorerResult] = []
        for i, lst in enumerate(listings):
            s = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=scores[i],
                    components={
                        "building_violation_count": s["building_violation_count"],
                        "building_hpd_class_a": s["building_hpd_class_a"],
                        "building_hpd_class_b": s["building_hpd_class_b"],
                        "building_hpd_class_c": s["building_hpd_class_c"],
                        "building_unitsres": s["building_unitsres"],
                        "building_violations_per_unit": round(per_units[i], 4),
                        "building_active_permits": s["building_active_permits"],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _fetch_dob_violations(self, boro: str, block: str, lot: str) -> list[dict]:
        return self._store.query(
            "dob_violations",
            where_clause="boro=? AND block=? AND lot=?",
            params=(boro, block, lot),
            select="isn_dob_bis_viol",
        )

    def _fetch_hpd_violations(
        self, boro: str, block: str, lot: str,
    ) -> tuple[int, int, int]:
        """Return (class_a, class_b, class_c) counts for open HPD violations."""
        # HPD violations store block/lot WITHOUT leading zeros,
        # but parse_bbl returns zero-padded values — strip them.
        rows = self._store.query(
            "hpd_violations",
            where_clause="boroid=? AND block=? AND lot=?",
            params=(boro, str(int(block)), str(int(lot))),
            select="class",
        )
        a = b = c = 0
        for r in rows:
            cls = (r.get("class") or "").upper()
            if cls == "A":
                a += 1
            elif cls == "B":
                b += 1
            elif cls == "C":
                c += 1
        return a, b, c

    def _fetch_permits(self, boro: str, block: str, lot: str) -> int:
        """Count active DOB permits for this BBL."""
        # DOB permits store borough as name, we have numeric boro code
        _BORO_MAP = {
            "1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN",
            "4": "QUEENS", "5": "STATEN ISLAND",
        }
        boro_name = _BORO_MAP.get(boro, "")
        if not boro_name:
            return 0
        rows = self._store.query(
            "dob_permits",
            where_clause="borough=? AND block=? AND lot=?",
            params=(boro_name, block, lot),
            select="COUNT(*) as cnt",
        )
        return int(rows[0]["cnt"]) if rows else 0


