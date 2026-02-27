"""
RentStabilizedScorer — flags buildings likely to contain rent-stabilized units.

Heuristic: buildings with 6+ residential units built before 1974 are
subject to NYC Rent Stabilization Law.  This is a building-level
indicator, not a unit-level guarantee.

Reuses the shared PLUTO cache (source="pluto") already populated by
FloodRiskScorer / BuildingViolationsScorer.

Output columns:
    rent_stabilized  INTEGER  (1 = likely, 0 = unlikely/unknown)
    building_year    INTEGER  (year built from PLUTO)
"""

from __future__ import annotations

import sqlite3

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult

# NYC Rent Stabilization thresholds
_YEAR_THRESHOLD = 1974
_UNITS_THRESHOLD = 6


class RentStabilizedScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "rent_stabilized"

    def columns(self) -> dict[str, str]:
        return {
            "rent_stabilized": "INTEGER",
            "building_year": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("pluto", quiet=True)

        gh_map: dict[str, tuple[float, float]] = {}
        for lst in listings:
            gh_map.setdefault(lst["geohash"], (lst["lat"], lst["lon"]))

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "rent_stabilized")
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

            nearest = self._find_nearest_lot(pluto_rows, lat, lon)
            if nearest is None:
                stats = {"rent_stabilized": 0, "building_year": 0}
            else:
                year = int(float(nearest.get("yearbuilt") or 0))
                units = int(float(nearest.get("unitsres") or 0))
                stabilized = 1 if (year > 0 and year < _YEAR_THRESHOLD and units >= _UNITS_THRESHOLD) else 0
                stats = {"rent_stabilized": stabilized, "building_year": year}

            block_stats[gh] = stats
            self._cache.put(gh, "rent_stabilized", stats)

        results: list[ScorerResult] = []
        for lst in listings:
            stats = block_stats[lst["geohash"]]
            # Score: 100 if stabilized, 0 if not (informational only)
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=100.0 if stats["rent_stabilized"] else 0.0,
                    components={
                        "rent_stabilized": stats["rent_stabilized"],
                        "building_year": stats["building_year"],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------

    # PLUTO fetch removed — now uses DataStore via shared cache above

    @staticmethod
    def _find_nearest_lot(
        pluto_rows: list[dict],
        lat: float,
        lon: float,
    ) -> dict | None:
        if not pluto_rows:
            return None
        best, best_d = None, float("inf")
        for row in pluto_rows:
            try:
                rlat = float(row["latitude"])
                rlon = float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            d = haversine((lat, lon), (rlat, rlon), unit=Unit.METERS)
            if d < best_d:
                best, best_d = row, d
        return best
