"""
ParksScorer — scores listings by proximity AND size of nearby parks.

Uses pre-downloaded NYC Parks Properties dataset (stored locally in
``ds_parks`` table with MultiPolygon geometries and computed centroids).

Scoring model — *effective-score-per-park, best wins*:

Each park receives a quality multiplier and an influence radius ("reach")
based on its approximate acreage (derived from polygon geometry via the
Shoelace formula).

    Tier        Acres       Quality     Reach (m)
    ───────     ─────────   ─────────   ─────────
    Tiny        < 0.5       0.55        300
    Small       0.5 – 3     0.70        500
    Medium      3 – 15      0.85        800
    Large       15 – 100    0.95        1 200
    Flagship    100+        1.00        2 000

For each park within reach:
    proximity  = max(0, 1 − dist / reach)
    effective  = proximity × quality × 100

The listing's final score is the *maximum* effective score across all
nearby parks (capped at 100).  This ensures that a small triangle next
to your door never out-scores Central Park one block away.
"""

from __future__ import annotations

import json
import math
import sqlite3
from typing import NamedTuple

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult

# ── Size tiers ──────────────────────────────────────────────────────
#                   (min_acres, quality, reach_m)
_TIERS: list[tuple[float, float, int]] = [
    (100.0, 1.00, 2000),   # Flagship  (Central Park, Prospect Park …)
    (15.0,  0.95, 1200),   # Large     (Fort Tryon, Astoria Park …)
    (3.0,   0.85,  800),   # Medium    (Tompkins Square, McCarren …)
    (0.5,   0.70,  500),   # Small     (playgrounds, community gardens)
    (0.0,   0.55,  300),   # Tiny      (triangles, sitting areas)
]

_ACRES_PER_SQM = 0.000247105

# Widest reach across tiers — controls the bbox search radius.
_MAX_REACH_M = max(t[2] for t in _TIERS)
# Convert to degrees for the centroid pre-filter (generous).
_SEARCH_DELTA = _MAX_REACH_M / 111_320 * 1.5  # ≈0.027°


class _ParkHit(NamedTuple):
    """A single park's contribution to a listing's score."""
    name: str
    distance_m: int
    acres: float
    effective_score: float


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
            "parks_acres": "REAL",
        }

    # ─── public entry point ─────────────────────────────────────────

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
            hit = self._best_park(parks, lat, lon)
            stats = {
                "parks_distance_m": hit.distance_m,
                "parks_name": hit.name,
                "parks_acres": round(hit.acres, 2),
                "_effective": hit.effective_score,
            }
            block_stats[gh] = stats
            self._cache.put(gh, "parks", stats)

        results: list[ScorerResult] = []
        for lst in listings:
            stats = block_stats[lst["geohash"]]
            sc = round(stats["_effective"], 1)

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=sc,
                    components={
                        "parks_distance_m": stats["parks_distance_m"],
                        "parks_name": stats["parks_name"],
                        "parks_acres": stats["parks_acres"],
                    },
                )
            )
        return results

    # ─── internals ──────────────────────────────────────────────────

    def _query_nearby_parks(self, lat: float, lon: float) -> list[dict]:
        """Fetch parks within the maximum reach using centroid pre-filter."""
        rows = self._store.query_bbox(
            "parks", lat, lon, delta=_SEARCH_DELTA,
            select="name311, multipolygon",
            lat_col="centroid_lat",
            lon_col="centroid_lon",
        )
        for r in rows:
            mp = r.get("multipolygon")
            if mp and isinstance(mp, str):
                try:
                    r["multipolygon"] = json.loads(mp)
                except json.JSONDecodeError:
                    r["multipolygon"] = None
        return rows

    @staticmethod
    def _best_park(
        parks: list[dict],
        lat: float,
        lon: float,
    ) -> _ParkHit:
        """Return the single best-scoring park (proximity × size quality)."""
        best = _ParkHit(name="", distance_m=9999, acres=0.0, effective_score=0.0)

        for p in parks:
            geom = p.get("multipolygon")
            if not geom or not isinstance(geom, dict):
                continue

            # Nearest border distance
            dist_m = _nearest_border_dist(geom, lat, lon)
            # Approximate acreage from polygon area
            acres = _polygon_area_acres(geom)
            # Look up tier
            quality, reach = _tier_params(acres)

            if dist_m >= reach:
                continue  # park is out of effective range

            proximity = max(0.0, 1.0 - dist_m / reach)
            eff = proximity * quality * 100.0

            if eff > best.effective_score:
                best = _ParkHit(
                    name=p.get("name311", ""),
                    distance_m=int(dist_m),
                    acres=acres,
                    effective_score=eff,
                )

        return best


# ── helper functions ────────────────────────────────────────────────

def _tier_params(acres: float) -> tuple[float, int]:
    """Return (quality, reach_m) for the given acreage."""
    for min_ac, quality, reach in _TIERS:
        if acres >= min_ac:
            return quality, reach
    # Fallback (should not happen as last tier starts at 0)
    return 0.55, 300


def _nearest_border_dist(geom: dict, lat: float, lon: float) -> float:
    """Minimum haversine distance (m) from (lat,lon) to any polygon vertex."""
    coords = _extract_coords(geom)
    best = float("inf")
    for clon, clat in coords:
        d = haversine((lat, lon), (clat, clon), unit=Unit.METERS)
        if d < best:
            best = d
    return best


def _polygon_area_acres(geom: dict) -> float:
    """Approximate acreage of a GeoJSON Polygon/MultiPolygon via Shoelace."""
    gtype = geom.get("type", "")
    raw = geom.get("coordinates", [])
    total_sqm = 0.0

    if gtype == "MultiPolygon":
        for poly in raw:
            if poly:
                total_sqm += _ring_area_sqm(poly[0])
    elif gtype == "Polygon":
        if raw:
            total_sqm += _ring_area_sqm(raw[0])
    return total_sqm * _ACRES_PER_SQM


def _ring_area_sqm(ring: list[list[float]]) -> float:
    """Shoelace area (m²) for a ring of [lon, lat] pairs."""
    n = len(ring)
    if n < 3:
        return 0.0
    avg_lat = sum(pt[1] for pt in ring) / n
    m_lat = 111_320.0
    m_lon = 111_320.0 * math.cos(math.radians(avg_lat))
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        x1, y1 = ring[i][0] * m_lon, ring[i][1] * m_lat
        x2, y2 = ring[j][0] * m_lon, ring[j][1] * m_lat
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


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
