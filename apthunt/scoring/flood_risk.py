"""
FloodRiskScorer — scores listings by FEMA flood zone status.

Uses PLUTO data (fetched via SodaClient, cached in BlockCache) to
check the firm07_flag and pfirm15_flag fields for the nearest tax lot.

Score: 100 if NOT in a flood zone, 0 if flagged.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from haversine import haversine as _hav, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.soda_client import SodaClient
from apthunt.scoring.base import Scorer, ScorerResult


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two lat/lon points."""
    return _hav((lat1, lon1), (lat2, lon2), unit=Unit.METERS)


class FloodRiskScorer(Scorer):

    PLUTO_SELECT = (
        "bbl,address,yearbuilt,numfloors,unitsres,"
        "firm07_flag,pfirm15_flag,latitude,longitude"
    )

    def __init__(self, soda: SodaClient, cache: BlockCache):
        self._soda = soda
        self._cache = cache

    @property
    def name(self) -> str:
        return "flood_risk"

    def columns(self) -> dict[str, str]:
        return {
            "flood_firm07": "TEXT",
            "flood_pfirm15": "TEXT",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        results = []

        for lst in listings:
            gh = lst["geohash"]
            lat, lon = lst["lat"], lst["lon"]

            pluto = self._cache.get_or_fetch(
                gh,
                "pluto",
                lambda lat=lat, lon=lon: self._fetch_pluto(lat, lon),
            )

            firm07, pfirm15 = self._find_nearest_flags(pluto, lat, lon)

            in_flood_zone = bool(firm07) or bool(pfirm15)
            score = 0.0 if in_flood_zone else 100.0

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "flood_firm07": firm07 or "",
                        "flood_pfirm15": pfirm15 or "",
                    },
                )
            )

        return results

    def _fetch_pluto(self, lat: float, lon: float) -> list[dict]:
        """Fetch PLUTO lots in a ~300m bounding box around the point."""
        # 0.0015° ≈ 150m at NYC latitude — covers the geohash cell
        delta = 0.0015
        return self._soda.query_bbox(
            "pluto",
            "latitude",
            "longitude",
            lat - delta,
            lat + delta,
            lon - delta,
            lon + delta,
            select=self.PLUTO_SELECT,
        )

    @staticmethod
    def _find_nearest_flags(
        pluto_rows: list[dict],
        lat: float,
        lon: float,
    ) -> tuple[str, str]:
        """Find the nearest PLUTO lot and return its flood flags."""
        if not pluto_rows:
            return ("", "")

        best_dist = float("inf")
        best_firm07 = ""
        best_pfirm15 = ""

        for row in pluto_rows:
            try:
                rlat = float(row.get("latitude", 0))
                rlon = float(row.get("longitude", 0))
            except (ValueError, TypeError):
                continue

            d = _haversine(lat, lon, rlat, rlon)
            if d < best_dist:
                best_dist = d
                best_firm07 = row.get("firm07_flag", "") or ""
                best_pfirm15 = row.get("pfirm15_flag", "") or ""

        return (best_firm07, best_pfirm15)
