"""
ParksScorer — scores listings by distance to nearest park border.

Uses NYC Parks Properties dataset (MultiPolygon geometries).
Computes minimum distance from listing to any vertex on the nearest
park polygon border — not the centroid.

Scoring (metres to nearest park border):
    ≤ 100 m  → 100
    ≤ 300 m  →  80
    ≤ 600 m  →  60
    ≤ 1000 m →  40
    > 1000 m →  20
"""

from __future__ import annotations

import sqlite3

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.soda_client import SodaClient
from apthunt.scoring.base import Scorer, ScorerResult


class ParksScorer(Scorer):

    def __init__(self, soda: SodaClient, cache: BlockCache):
        self._soda = soda
        self._cache = cache

    @property
    def name(self) -> str:
        return "parks"

    def columns(self) -> dict[str, str]:
        return {
            "parks_distance_m": "INTEGER",
            "parks_name": "TEXT",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        # Deduplicate by geohash
        gh_map: dict[str, tuple[float, float]] = {}
        for lst in listings:
            gh_map.setdefault(lst["geohash"], (lst["lat"], lst["lon"]))

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "parks")
            if cached is not None:
                block_stats[gh] = cached
                continue

            parks = self._fetch_parks(lat, lon)
            dist, name = self._nearest_border(parks, lat, lon)
            stats = {"parks_distance_m": dist, "parks_name": name}
            block_stats[gh] = stats
            self._cache.put(gh, "parks", stats)

        results: list[ScorerResult] = []
        for lst in listings:
            stats = block_stats[lst["geohash"]]
            dist = stats["parks_distance_m"]
            name = stats["parks_name"]

            if dist <= 100:
                sc = 100.0
            elif dist <= 300:
                sc = 80.0
            elif dist <= 600:
                sc = 60.0
            elif dist <= 1000:
                sc = 40.0
            else:
                sc = 20.0

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=sc,
                    components={
                        "parks_distance_m": dist,
                        "parks_name": name,
                    },
                )
            )
        return results

    # ------------------------------------------------------------------

    def _fetch_parks(self, lat: float, lon: float) -> list[dict]:
        """Fetch parks within 1.5 km via within_circle on multipolygon."""
        return self._soda.query_circle(
            dataset="parks",
            geo_column="multipolygon",
            lat=lat,
            lon=lon,
            radius_m=1500,
            select="name311, multipolygon",
        )

    @staticmethod
    def _nearest_border(
        parks: list[dict],
        lat: float,
        lon: float,
    ) -> tuple[int, str]:
        """Return (distance_m, park_name) for the nearest park border vertex."""
        best_dist = float("inf")
        best_name = ""

        for p in parks:
            geom = p.get("multipolygon")
            if not geom or not isinstance(geom, dict):
                continue

            coords = _extract_coords(geom)
            for clon, clat in coords:
                d = haversine((lat, lon), (clat, clon), unit=Unit.METERS)
                if d < best_dist:
                    best_dist = d
                    best_name = p.get("name311", "")

        return (
            int(best_dist) if best_dist != float("inf") else 9999,
            best_name,
        )


def _extract_coords(geom: dict) -> list[tuple[float, float]]:
    """Extract all [lon, lat] vertex pairs from a GeoJSON geometry."""
    gtype = geom.get("type", "")
    raw = geom.get("coordinates", [])
    points: list[tuple[float, float]] = []

    if gtype == "MultiPolygon":
        for polygon in raw:
            for ring in polygon:
                points.extend(ring)
    elif gtype == "Polygon":
        for ring in raw:
            points.extend(ring)
    elif gtype == "Point":
        points.append(tuple(raw))

    return points
