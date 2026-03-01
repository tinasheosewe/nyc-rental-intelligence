"""
PestScorer — scores listings by pest / vermin exposure.

Combines two independent data sources:

1. **Building-level** — HPD complaints categorised as
   ``major_category='UNSANITARY CONDITION'`` and
   ``minor_category='PESTS'``, looked up by the building's BBL
   (via PLUTO, same approach as ManagementScorer).

2. **Area-level** — 311 Service Requests with ``complaint_type='Rodent'``
   within 100 m (tight radius — pests are hyper-local).

Scoring:
    Combined complaints are normalised **per residential unit**
    (via PLUTO ``unitsres``), then percentile-ranked.
    A 100-unit building with 5 complaints rates far better than
    a single home with 5.

Output columns:
    pest_hpd_count       INTEGER — HPD pest complaints for the building (12 mo)
    pest_rodent_count    INTEGER — 311 rodent complaints within 100 m
    pest_total           INTEGER — combined raw total
    pest_units           INTEGER — residential units from PLUTO (≥1)
    pest_per_unit        REAL    — pest_total / pest_units
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
    pluto_units,
)


RADIUS_M = 100


class PestScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "pest"

    def columns(self) -> dict[str, str]:
        return {
            "pest_hpd_count": "INTEGER",
            "pest_rodent_count": "INTEGER",
            "pest_total": "INTEGER",
            "pest_units": "INTEGER",
            "pest_per_unit": "REAL",
        }

    # ------------------------------------------------------------------
    # Main scoring entry point
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("pluto", quiet=True)
        self._store.ensure_downloaded("hpd_complaints", quiet=True)
        self._store.ensure_downloaded("noise", quiet=True)

        # Deduplicate by geohash (one lookup per block)
        gh_map = dedupe_by_geohash(listings)

        block_stats: dict[str, dict] = {}

        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "pest_v2")
            if cached is not None:
                block_stats[gh] = cached
                continue

            # --- PLUTO lookup (BBL + units) --------------------------------
            pluto_rows = self._store.query_bbox(
                "pluto", lat, lon, delta=0.0015,
            )
            nearest = find_nearest_row(pluto_rows, lat, lon)

            # --- Building-level: HPD pest complaints via BBL ---------------
            hpd_count = self._hpd_pest_count(nearest)

            # --- Area-level: 311 rodent complaints within 100 m ------------
            rodent_count = self._rodent_count(lat, lon)

            # --- Units from PLUTO (floor at 1) -----------------------------
            units = pluto_units(nearest)

            total = hpd_count + rodent_count
            per_unit = round(total / units, 4)

            stats = {
                "pest_hpd_count": hpd_count,
                "pest_rodent_count": rodent_count,
                "pest_total": total,
                "pest_units": units,
                "pest_per_unit": per_unit,
            }
            block_stats[gh] = stats
            self._cache.put(gh, "pest_v2", stats)

        # Percentile-rank by pest_per_unit (lower = better)
        # zero_is_perfect: no complaints → score 100.
        raw = [block_stats[lst["geohash"]]["pest_per_unit"] for lst in listings]
        pct_scores = percentile_scores(raw, reverse=True, zero_is_perfect=True)

        results: list[ScorerResult] = []
        for lst, pct in zip(listings, pct_scores):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=pct,
                    components={
                        "pest_hpd_count": stats["pest_hpd_count"],
                        "pest_rodent_count": stats["pest_rodent_count"],
                        "pest_total": stats["pest_total"],
                        "pest_units": stats["pest_units"],
                        "pest_per_unit": stats["pest_per_unit"],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _hpd_pest_count(self, nearest_lot: dict | None) -> int:
        """Count HPD pest complaints for the nearest PLUTO lot's BBL."""
        if nearest_lot is None:
            return 0

        raw_bbl = nearest_lot.get("bbl")
        if not raw_bbl:
            return 0

        bbl = normalize_bbl(raw_bbl)

        rows = self._store.query(
            "hpd_complaints",
            where_clause=(
                "bbl = ? "
                "AND major_category = 'UNSANITARY CONDITION' "
                "AND minor_category = 'PESTS'"
            ),
            params=(bbl,),
            select="COUNT(*) as cnt",
        )
        return int(rows[0]["cnt"]) if rows else 0

    def _rodent_count(self, lat: float, lon: float) -> int:
        """Count 311 rodent complaints within RADIUS_M metres."""
        rows = self._store.query_circle(
            "noise",
            lat=lat,
            lon=lon,
            radius_m=RADIUS_M,
            select="complaint_type",
        )
        return sum(1 for r in rows if r.get("complaint_type") == "Rodent")


