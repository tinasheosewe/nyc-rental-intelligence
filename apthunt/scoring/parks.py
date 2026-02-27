"""
ParksScorer — scores listings by distance to nearest park border.

Uses pre-downloaded NYC Parks Properties dataset (stored locally in
``ds_parks`` table with MultiPolygon geometries and computed centroids).

Scoring (continuous, based on distance):
    0 m     → 100
    1500 m+ →   0
    Linear interpolation in between.
"""

from __future__ import annotations

import json
import sqlite3

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult


class ParksScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
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
        self._store.ensure_downloaded("parks", quiet=True)

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

            parks = self._query_nearby_parks(lat, lon)
            dist, name = self._nearest_border(parks, lat, lon)
            stats = {"parks_distance_m": dist, "parks_name": name}
            block_stats[gh] = stats
            self._cache.put(gh, "parks", stats)

        results: list[ScorerResult] = []
        for lst in listings:
            stats = block_stats[lst["geohash"]]
            dist = stats["parks_distance_m"]
            name = stats["parks_name"]

            # Continuous linear: 100 at 0 m, 0 at ≥ 1500 m
            sc = round(max(0.0, 100.0 * (1.0 - dist / 1500.0)), 1)

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

    def _query_nearby_parks(self, lat: float, lon: float) -> list[dict]:
        """Fetch parks within ~2 km using centroid bbox pre-filter."""
        # Use a generous delta (0.02° ≈ 2.2 km) to catch parks
        # whose border reaches within 1.5 km even if centroid is farther
        rows = self._store.query_bbox(
            "parks", lat, lon, delta=0.02,
            select="name311, multipolygon",
            lat_col="centroid_lat",
            lon_col="centroid_lon",
        )
        # Parse the geometry JSON back into dicts
        for r in rows:
            mp = r.get("multipolygon")
            if mp and isinstance(mp, str):
                try:
                    r["multipolygon"] = json.loads(mp)
                except json.JSONDecodeError:
                    r["multipolygon"] = None
        return rows

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
