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
- **Evictions** — executed residential evictions within 500 m (12 mo).

Scoring:
    total_complaints (HPD + 311 heat) + litigation penalty (×10 each)
    normalised per residential unit, then percentile-ranked.
    Fewer complaints per unit → higher score.

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
    mgmt_evictions          INTEGER — residential evictions within 500 m (12 mo)
    mgmt_complaints_per_unit REAL   — complaint rate per unit
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import (
    dedupe_by_geohash,
    find_nearest_row,
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


class ManagementScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache
        self._owner_cache: dict[str, dict] = {}

    @property
    def name(self) -> str:
        return "management"

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
            "mgmt_complaints_per_unit": "REAL",
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
        EVICTION_RADIUS_M = 500

        gh_map = dedupe_by_geohash(listings)

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "management_v3")
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

            heat_311 = self._heat_311_count(lat, lon, HEAT_RADIUS_M)
            evictions = self._eviction_count(lat, lon, EVICTION_RADIUS_M)
            litigations = self._litigation_count(nearest)

            if nearest is None or not nearest.get("ownername"):
                stats = self._empty_stats(heat_311, evictions, litigations)
            else:
                owner = nearest["ownername"].strip()
                stats = self._get_owner_stats(
                    owner, heat_311, evictions, litigations,
                )

            block_stats[gh] = stats
            self._cache.put(gh, "management_v3", stats)

        raw_values = [block_stats[lst["geohash"]]["mgmt_complaints_per_unit"]
                      for lst in listings]
        pct_scores = percentile_scores(raw_values, reverse=True, zero_is_perfect=True)

        results: list[ScorerResult] = []
        for lst, pct in zip(listings, pct_scores):
            stats = block_stats[lst["geohash"]]
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

    @staticmethod
    def _empty_stats(heat_311: int, evictions: int, litigations: int) -> dict:
        return {
            "mgmt_owner": "",
            "mgmt_owner_buildings": 0,
            "mgmt_owner_units": 0,
            "mgmt_complaints": 0,
            "mgmt_hpd_heat": 0,
            "mgmt_hpd_plumbing": 0,
            "mgmt_hpd_paint": 0,
            "mgmt_hpd_safety": 0,
            "mgmt_heat_complaints": heat_311,
            "mgmt_litigations": litigations,
            "mgmt_evictions": evictions,
            "mgmt_complaints_per_unit": 0.0,
        }

    def _heat_311_count(self, lat: float, lon: float, radius_m: int) -> int:
        rows = self._store.query_circle(
            "noise", lat=lat, lon=lon, radius_m=radius_m,
            select="complaint_type",
        )
        return sum(1 for r in rows if r.get("complaint_type") == "HEAT/HOT WATER")

    def _eviction_count(self, lat: float, lon: float, radius_m: int) -> int:
        rows = self._store.query_circle(
            "evictions", lat=lat, lon=lon, radius_m=radius_m,
            select="court_index_number",
        )
        return len(rows)

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

    def _get_owner_stats(
        self,
        owner: str,
        heat_311: int,
        evictions: int,
        litigations: int,
    ) -> dict:
        if owner in self._owner_cache:
            cached = dict(self._owner_cache[owner])
            cached["mgmt_heat_complaints"] = heat_311
            cached["mgmt_evictions"] = evictions
            cached["mgmt_litigations"] = litigations
            total = cached["mgmt_complaints"] + heat_311 + litigations * 10
            units = max(cached["mgmt_owner_units"], 1)
            cached["mgmt_complaints_per_unit"] = round(total / units, 3)
            return cached

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
        total_complaints = 0
        cat_counts = {v: 0 for v in _CATEGORY_KEYS.values()}

        bbl_list = list(bbl_set)
        for i in range(0, len(bbl_list), 50):
            batch = bbl_list[i:i + 50]
            placeholders = ",".join("?" * len(batch))
            rows = self._store.query(
                "hpd_complaints",
                where_clause=f"bbl IN ({placeholders})",
                params=tuple(batch),
                select="major_category",
            )
            for r in rows:
                total_complaints += 1
                col = _CATEGORY_KEYS.get(r.get("major_category", ""))
                if col:
                    cat_counts[col] += 1

        total_all = total_complaints + heat_311 + litigations * 10
        cpu = total_all / max(total_units, 1)

        stats = {
            "mgmt_owner": owner,
            "mgmt_owner_buildings": total_buildings,
            "mgmt_owner_units": total_units,
            "mgmt_complaints": total_complaints,
            **cat_counts,
            "mgmt_heat_complaints": heat_311,
            "mgmt_litigations": litigations,
            "mgmt_evictions": evictions,
            "mgmt_complaints_per_unit": round(cpu, 3),
        }
        self._owner_cache[owner] = stats
        return stats


