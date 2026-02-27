"""
BuildingViolationsScorer — scores listings by active DOB building violations.

Uses PLUTO (shared cache with FloodRiskScorer) to map lat/lon → BBL,
then queries DOB Violations for that BBL.  Normalises count by
residential units so large buildings aren't unfairly penalised.

Scoring (violations per unit):
    0        → 100
    ≤ 0.05   →  80
    ≤ 0.1    →  60
    ≤ 0.2    →  40
    > 0.2    →  20
"""

from __future__ import annotations

import sqlite3

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.soda_client import SodaClient
from apthunt.scoring.base import Scorer, ScorerResult


# PLUTO select — superset of what FloodRiskScorer uses so the shared
# cache entry works for both scorers.
_PLUTO_SELECT = (
    "bbl,address,yearbuilt,numfloors,unitsres,"
    "firm07_flag,pfirm15_flag,latitude,longitude"
)


class BuildingViolationsScorer(Scorer):

    def __init__(self, soda: SodaClient, cache: BlockCache):
        self._soda = soda
        self._cache = cache

    @property
    def name(self) -> str:
        return "building_violations"

    def columns(self) -> dict[str, str]:
        return {
            "building_violation_count": "INTEGER",
            "building_unitsres": "INTEGER",
            "building_violations_per_unit": "REAL",
        }

    # ------------------------------------------------------------------
    # Main scoring loop
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        # Deduplicate by geohash so each block is fetched once
        gh_map: dict[str, tuple[float, float]] = {}
        for lst in listings:
            gh_map.setdefault(lst["geohash"], (lst["lat"], lst["lon"]))

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "building_violations")
            if cached is not None:
                block_stats[gh] = cached
                continue

            # 1. Get PLUTO lots (reuse shared "pluto" cache)
            pluto_rows = self._cache.get_or_fetch(
                gh,
                "pluto",
                lambda lat=lat, lon=lon: self._fetch_pluto(lat, lon),
            )

            # 2. Find the nearest lot to the listing point
            nearest = self._find_nearest_lot(pluto_rows, lat, lon)
            if nearest is None:
                stats = {
                    "building_violation_count": 0,
                    "building_unitsres": 1,
                }
                block_stats[gh] = stats
                self._cache.put(gh, "building_violations", stats)
                continue

            bbl_raw = nearest.get("bbl", "")
            unitsres = max(1, int(float(nearest.get("unitsres") or 0)))

            # 3. Parse BBL → boro / block / lot for DOB query
            try:
                bbl_str = str(int(float(bbl_raw))).zfill(10)
                boro = bbl_str[0]
                block = bbl_str[1:6]
                lot = bbl_str[6:10]
            except (ValueError, IndexError):
                stats = {
                    "building_violation_count": 0,
                    "building_unitsres": unitsres,
                }
                block_stats[gh] = stats
                self._cache.put(gh, "building_violations", stats)
                continue

            # 4. Query DOB violations (active only)
            violations = self._fetch_violations(boro, block, lot)
            count = len(violations)

            stats = {
                "building_violation_count": count,
                "building_unitsres": unitsres,
            }
            block_stats[gh] = stats
            self._cache.put(gh, "building_violations", stats)

        # Build results
        results: list[ScorerResult] = []
        for lst in listings:
            stats = block_stats[lst["geohash"]]
            count = stats["building_violation_count"]
            unitsres = stats.get("building_unitsres", 1)
            per_unit = count / max(1, unitsres)

            if per_unit == 0:
                sc = 100.0
            elif per_unit <= 0.05:
                sc = 80.0
            elif per_unit <= 0.1:
                sc = 60.0
            elif per_unit <= 0.2:
                sc = 40.0
            else:
                sc = 20.0

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=sc,
                    components={
                        "building_violation_count": count,
                        "building_unitsres": unitsres,
                        "building_violations_per_unit": round(per_unit, 4),
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _fetch_pluto(self, lat: float, lon: float) -> list[dict]:
        """Fetch PLUTO lots in a ~300 m bbox (same query as FloodRiskScorer)."""
        delta = 0.0015
        return self._soda.query_bbox(
            "pluto",
            "latitude",
            "longitude",
            lat - delta,
            lat + delta,
            lon - delta,
            lon + delta,
            select=_PLUTO_SELECT,
        )

    @staticmethod
    def _find_nearest_lot(
        pluto_rows: list[dict],
        lat: float,
        lon: float,
    ) -> dict | None:
        """Return the PLUTO row closest to (lat, lon)."""
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

    def _fetch_violations(
        self,
        boro: str,
        block: str,
        lot: str,
    ) -> list[dict]:
        """Fetch active DOB violations for a given BBL."""
        where = (
            f"boro='{boro}' AND block='{block}' AND lot='{lot}' "
            f"AND violation_category LIKE '%ACTIVE%'"
        )
        return self._soda.query(
            "dob_violations",
            where=where,
            select="isn_dob_bis_viol,violation_type,violation_category,issue_date",
        )
