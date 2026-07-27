"""
ParksScorer — scores listings by proximity AND size of nearby parks.

Uses pre-downloaded NYC Parks Properties dataset (stored locally in
``ds_parks`` table with MultiPolygon geometries and computed centroids).

Scoring model — *effective-score-per-park, best wins*:

Each park receives a quality multiplier and an influence radius ("reach")
based on its acreage (dataset ``acres`` column when available, else
approximated from polygon geometry via the Shoelace formula).

    Tier        Acres       Quality     Reach (m)
    ───────     ─────────   ─────────   ─────────
    Tiny        < 0.5       0.55        300
    Small       0.5 – 3     0.70        500
    Medium      3 – 15      0.85        800
    Large       15 – 100    0.95        1 200
    Flagship    100+        1.00        2 000

Two nuance layers on top of the size tiers:

**Typecategory gate** (``typecategory`` column, added by re-download —
degrades to neutral ×1.0 while the column is absent): a paved triangle,
mall strip, parkway median, or cemetery is not a park experience.
Triangle/Plaza/Mall/Strip/Parkway/Cemetery ×0.1, Playground ×0.7,
Nature Area ×0.9, Neighborhood/Community/Flagship Park ×1.0.

**Pedestrian severance**: the proximity curve runs on
``effective_distance = haversine + path_severance_penalty_m(...)`` —
a park across a six-lane trunk road is not experientially 200 m away.

For each park within reach:
    proximity  = max(0, 1 − effective_dist / reach)
    effective  = proximity × quality × type_mult × 100

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
from apthunt.scoring.baseline import baseline_scores
from apthunt.scoring.utils import dedupe_by_geohash, path_severance_penalty_m

# Cache version — v3: typecategory gate + severance-adjusted distance
# (raw metric semantics changed; rebaseline follows this wave).
_CACHE_KEY = "parks_v4"

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

# ── Typecategory gate ───────────────────────────────────────────────
# NYC Parks "typecategory" → scoring multiplier.  Triangles, plazas,
# malls, medians, parkway strips and cemeteries are green on a map but
# not parks for living next to; playgrounds are parks-for-some.
# Unknown / missing categories stay neutral (×1.0), which also covers
# today's DB where the column hasn't been re-downloaded yet.
_TYPE_MULT: dict = {
    "triangle/plaza": 0.1,
    "triangle": 0.1,
    "plaza": 0.1,
    "mall": 0.1,
    "strip": 0.1,
    "parkway": 0.1,
    "cemetery": 0.1,
    "playground": 0.7,
    "jointly operated playground": 0.7,
    "neighborhood park": 1.0,
    "community park": 1.0,
    "flagship park": 1.0,
    "nature area": 0.9,
}


def _type_multiplier(typecategory) -> float:
    """Scoring multiplier for a park's typecategory (neutral when unknown)."""
    if not typecategory:
        return 1.0
    return _TYPE_MULT.get(str(typecategory).strip().lower(), 1.0)


class _ParkHit(NamedTuple):
    """A single park's contribution to a listing's score."""
    name: str
    distance_m: int
    acres: float
    typecategory: str
    type_mult: float
    severance_m: float
    effective_score: float


_NO_PARK = _ParkHit(
    name="", distance_m=9999, acres=0.0,
    typecategory="", type_mult=1.0, severance_m=0.0,
    effective_score=0.0,
)


class ParksScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "parks"

    # Baseline metric: raw effective park-access score
    # (proximity × quality × type_mult, severance-adjusted distance).
    # Higher = better access, and 0 (no park in reach) is the worst case.
    baseline_component = "parks_effective"
    baseline_reverse = False
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "parks_distance_m": "INTEGER",
            "parks_name": "TEXT",
            "parks_acres": "REAL",
            "parks_effective": "REAL",
            "parks_typecategory": "TEXT",
            "parks_type_mult": "REAL",
            "parks_severance_m": "REAL",
        }

    # ─── public entry point ─────────────────────────────────────────

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("parks", quiet=True)
        # Severance uses ds_roads opportunistically: the dataset refresh
        # pipeline owns its (heavy, Overpass) download, and
        # path_severance_penalty_m degrades to 0.0 while it's absent —
        # deliberately NOT ensure_downloaded("roads") here.

        # Probe once per score() call which re-download columns exist yet.
        has_type, has_acres = self._probe_extra_cols()

        # Deduplicate by geohash
        gh_map = dedupe_by_geohash(listings)

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, _CACHE_KEY)
            if cached is not None:
                block_stats[gh] = cached
                continue

            parks = self._query_nearby_parks(lat, lon, has_type, has_acres)
            hit = self._best_park(parks, lat, lon)
            stats = {
                "parks_distance_m": hit.distance_m,
                "parks_name": hit.name,
                "parks_acres": round(hit.acres, 2),
                "parks_typecategory": hit.typecategory,
                "parks_type_mult": hit.type_mult,
                "parks_severance_m": hit.severance_m,
                "_effective": hit.effective_score,
            }
            block_stats[gh] = stats
            self._cache.put(gh, _CACHE_KEY, stats)

        # Absolute scoring: rank the raw effective-access metric against the
        # frozen citywide baseline; fall back to the raw effective score
        # (already 0-100) until the first baseline build.
        raw_values = [block_stats[lst["geohash"]]["_effective"] for lst in listings]
        scores = baseline_scores(
            conn, self.name, raw_values, reverse=False, zero_is_perfect=False
        )
        if scores is None:
            scores = [round(v, 1) for v in raw_values]

        results: list[ScorerResult] = []
        for lst, sc, raw in zip(listings, scores, raw_values):
            stats = block_stats[lst["geohash"]]

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=sc,
                    components={
                        "parks_distance_m": stats["parks_distance_m"],
                        "parks_name": stats["parks_name"],
                        "parks_acres": stats["parks_acres"],
                        "parks_effective": round(raw, 1),
                        "parks_typecategory": stats["parks_typecategory"],
                        "parks_type_mult": stats["parks_type_mult"],
                        "parks_severance_m": stats["parks_severance_m"],
                    },
                )
            )
        return results

    # ─── internals ──────────────────────────────────────────────────

    def _probe_extra_cols(self) -> tuple[bool, bool]:
        """Whether ds_parks has the re-download columns (typecategory, acres).

        RE-DOWNLOADS ARE IN FLIGHT: today's table may predate them, so
        probe with a cheap per-column SELECT and degrade gracefully.
        """
        has = []
        for col in ("typecategory", "acres"):
            try:
                self._store.query("parks", select=f"[{col}]", limit=1)
                has.append(True)
            except Exception:
                has.append(False)
        return has[0], has[1]

    def _query_nearby_parks(
        self,
        lat: float,
        lon: float,
        has_type: bool,
        has_acres: bool,
    ) -> list[dict]:
        """Fetch parks within the maximum reach using centroid pre-filter."""
        select = "name311, multipolygon, centroid_lat, centroid_lon"
        if has_type:
            select += ", typecategory"
        if has_acres:
            select += ", acres"
        rows = self._store.query_bbox(
            "parks", lat, lon, delta=_SEARCH_DELTA,
            select=select,
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

    def _best_park(
        self,
        parks: list[dict],
        lat: float,
        lon: float,
    ) -> _ParkHit:
        """Return the single best-scoring park.

        Severance is the expensive part (path sampling against ds_roads),
        so candidates are first ranked by their *optimistic* score (no
        severance — the penalty can only lower a score) and evaluated in
        that order; once the best confirmed score beats every remaining
        optimistic bound, we stop.
        """
        candidates: list[tuple] = []
        for p in parks:
            geom = p.get("multipolygon")
            if not geom or not isinstance(geom, dict):
                continue

            # Nearest border distance (physical)
            dist_m = _nearest_border_dist(geom, lat, lon)
            # Acreage: dataset column when present, else polygon-derived
            acres = _park_acres(p, geom)
            # Typecategory gate (neutral ×1.0 when the column is absent)
            typecategory = str(p.get("typecategory") or "").strip()
            type_mult = _type_multiplier(typecategory)
            # Look up size tier
            quality, reach = _tier_params(acres)

            if dist_m >= reach:
                continue  # park is out of range even before severance

            optimistic = (1.0 - dist_m / reach) * quality * type_mult * 100.0
            if optimistic <= 0.0:
                continue
            candidates.append(
                (optimistic, dist_m, acres, typecategory, type_mult,
                 quality, reach, p)
            )

        candidates.sort(key=lambda c: c[0], reverse=True)

        best = _NO_PARK
        for (optimistic, dist_m, acres, typecategory, type_mult,
             quality, reach, p) in candidates:
            if optimistic <= best.effective_score:
                break  # sorted descending: no remaining candidate can win

            # Severance-adjusted distance BEFORE the proximity curve:
            # a park across a trunk road is not 200m away.
            severance_m = self._severance_m(lat, lon, p)
            eff_dist = dist_m + severance_m
            if eff_dist >= reach:
                continue  # severed out of effective range

            proximity = 1.0 - eff_dist / reach
            eff = proximity * quality * type_mult * 100.0
            if eff > best.effective_score:
                best = _ParkHit(
                    name=p.get("name311", ""),
                    distance_m=int(dist_m),
                    acres=acres,
                    typecategory=typecategory,
                    type_mult=type_mult,
                    severance_m=round(severance_m, 1),
                    effective_score=eff,
                )

        return best

    def _severance_m(self, lat: float, lon: float, park_row: dict) -> float:
        """Pedestrian-severance penalty listing→park centroid (0.0 on any
        missing data — degrade gracefully)."""
        try:
            clat = float(park_row["centroid_lat"])
            clon = float(park_row["centroid_lon"])
        except (KeyError, TypeError, ValueError):
            return 0.0
        try:
            return path_severance_penalty_m(self._store, lat, lon, clat, clon)
        except Exception:
            return 0.0


# ── helper functions ────────────────────────────────────────────────

def _tier_params(acres: float) -> tuple[float, int]:
    """Return (quality, reach_m) for the given acreage."""
    for min_ac, quality, reach in _TIERS:
        if acres >= min_ac:
            return quality, reach
    # Fallback (should not happen as last tier starts at 0)
    return 0.55, 300


def _park_acres(row: dict, geom: dict) -> float:
    """Park acreage: dataset ``acres`` column when present and positive
    (added by re-download), else Shoelace-derived from the polygon."""
    try:
        a = float(row.get("acres"))
        if a > 0:
            return a
    except (TypeError, ValueError):
        pass
    return _polygon_area_acres(geom)


def _nearest_border_dist(geom: dict, lat: float, lon: float) -> float:
    """Minimum distance (m) from (lat,lon) to any polygon EDGE.

    Point-to-segment, not point-to-vertex: on sparse polygons (some park
    rows are simplified to 4-corner quads) vertex distance overstates a
    mid-edge doorstep by up to half an edge length (measured +31m on
    Cooper Park's Maspeth Ave side).  Equirectangular projection around
    the query point is accurate to well under 1m at edge scales.
    """
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))

    def _seg_dist(a, b) -> float:
        ax = (a[0] - lon) * m_per_deg_lon
        ay = (a[1] - lat) * m_per_deg_lat
        bx = (b[0] - lon) * m_per_deg_lon
        by = (b[1] - lat) * m_per_deg_lat
        dx, dy = bx - ax, by - ay
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq <= 0.0:
            return math.hypot(ax, ay)
        t = max(0.0, min(1.0, -(ax * dx + ay * dy) / seg_len_sq))
        return math.hypot(ax + t * dx, ay + t * dy)

    best = float("inf")
    for ring in _extract_rings(geom):
        for i in range(len(ring) - 1):
            d = _seg_dist(ring[i], ring[i + 1])
            if d < best:
                best = d
        # Close the ring if the data didn't repeat the first vertex
        if len(ring) >= 2 and ring[0] != ring[-1]:
            d = _seg_dist(ring[-1], ring[0])
            if d < best:
                best = d
    return best


def _extract_rings(geom: dict) -> list:
    """All rings (outer + holes) as lists of (lon, lat) pairs."""
    gtype = geom.get("type", "")
    raw = geom.get("coordinates", [])
    rings = []
    if gtype == "MultiPolygon":
        for poly in raw:
            rings.extend(r for r in poly if r)
    elif gtype == "Polygon":
        rings.extend(r for r in raw if r)
    return rings


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
