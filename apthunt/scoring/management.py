"""
ManagementScorer — scores buildings by owner/management company reputation.

Uses PLUTO (``ds_pluto``) to identify the building's owner (``ownername``),
then queries HPD Complaints (``ds_hpd_complaints``) across **all** buildings
owned by that entity to produce a portfolio-level complaint rate.

This answers: "How well does this management company maintain its buildings?"

Scoring:
    For the listing's owner:
      1. Find all BBLs they own (PLUTO).
      2. Sum residential units across portfolio.
      3. Count HPD complaints (last 12 mo) across portfolio.
      4. complaints_per_unit = complaints / units.
    Percentile-rank across all owners seen in this batch.
    Fewer complaints per unit → higher score.

Output columns:
    mgmt_owner           TEXT   — owner name from PLUTO
    mgmt_owner_buildings INTEGER — total buildings in portfolio
    mgmt_owner_units     INTEGER — total residential units in portfolio
    mgmt_complaints      INTEGER — HPD complaints (12 mo) across portfolio
    mgmt_complaints_per_unit REAL — complaint rate per unit
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
    percentile_scores,
)


class ManagementScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache
        # Memo: owner_name → portfolio stats (built lazily, shared across geohashes)
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
            "mgmt_complaints_per_unit": "REAL",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("pluto", quiet=True)
        self._store.ensure_downloaded("hpd_complaints", quiet=True)

        # Deduplicate by geohash
        gh_map = dedupe_by_geohash(listings)

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "management")
            if cached is not None:
                block_stats[gh] = cached
                continue

            # Find nearest PLUTO lot
            pluto_rows = self._cache.get_or_fetch(
                gh,
                "pluto",
                lambda lat=lat, lon=lon: self._store.query_bbox(
                    "pluto", lat, lon, delta=0.0015,
                ),
            )
            nearest = find_nearest_row(pluto_rows, lat, lon)

            if nearest is None or not nearest.get("ownername"):
                stats = {
                    "mgmt_owner": "",
                    "mgmt_owner_buildings": 0,
                    "mgmt_owner_units": 0,
                    "mgmt_complaints": 0,
                    "mgmt_complaints_per_unit": 0.0,
                }
            else:
                owner = nearest["ownername"].strip()
                stats = self._get_owner_stats(owner)

            block_stats[gh] = stats
            self._cache.put(gh, "management", stats)

        # Percentile-rank by complaints_per_unit (lower = better)
        # zero_is_perfect: no complaints → score 100.
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
                    components={
                        "mgmt_owner": stats["mgmt_owner"],
                        "mgmt_owner_buildings": stats["mgmt_owner_buildings"],
                        "mgmt_owner_units": stats["mgmt_owner_units"],
                        "mgmt_complaints": stats["mgmt_complaints"],
                        "mgmt_complaints_per_unit": stats["mgmt_complaints_per_unit"],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_owner_stats(self, owner: str) -> dict:
        """Aggregate HPD complaints across all BBLs for an owner."""
        if owner in self._owner_cache:
            return self._owner_cache[owner]

        # 1. Find all BBLs for this owner
        bbls = self._store.query(
            "pluto",
            where_clause="ownername = ?",
            params=(owner,),
            select="bbl, unitsres",
        )

        total_units = 0
        total_buildings = len(bbls)
        total_complaints = 0
        bbl_set = set()

        for row in bbls:
            bbl = row.get("bbl")
            if bbl:
                # Normalize: PLUTO stores "1234567890.00000000", HPD uses "1234567890"
                    bbl_set.add(normalize_bbl(bbl))
            try:
                total_units += int(float(row.get("unitsres") or 0))
            except (ValueError, TypeError):
                pass

        # 2. Count HPD complaints across all these BBLs
        #    Query in batches to avoid huge IN clauses
        bbl_list = list(bbl_set)
        for i in range(0, len(bbl_list), 50):
            batch = bbl_list[i:i + 50]
            placeholders = ",".join("?" * len(batch))
            complaints = self._store.query(
                "hpd_complaints",
                where_clause=f"bbl IN ({placeholders})",
                params=tuple(batch),
                select="COUNT(*) as cnt",
            )
            if complaints:
                total_complaints += int(complaints[0].get("cnt", 0))

        cpu = (total_complaints / max(total_units, 1))

        stats = {
            "mgmt_owner": owner,
            "mgmt_owner_buildings": total_buildings,
            "mgmt_owner_units": total_units,
            "mgmt_complaints": total_complaints,
            "mgmt_complaints_per_unit": round(cpu, 3),
        }
        self._owner_cache[owner] = stats
        return stats


