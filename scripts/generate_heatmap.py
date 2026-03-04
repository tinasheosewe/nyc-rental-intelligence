#!/usr/bin/env python3
"""
Vectorized heatmap grid generator.

Instead of ~150K individual SQLite queries (≈3 hours), this version
loads each dataset ONCE into numpy arrays and uses:
  - 2D FFT convolution for "count within radius" (crime, noise, etc.)
  - KD-trees for nearest-neighbor and radius queries (transit, parks, pest)

Total runtime: ~10–30 seconds.

Output is identical in format to the previous per-cell version:
    frontend/public/heatmap/{crime,noise,transit,green_space,convenience,pest}.json

Algorithm notes:
  - Crime / noise / pest / greenery / convenience all use the pattern:
        1. One SQL query → load ALL records into numpy arrays
        2. Bin each record to its nearest grid cell  (O(n))
        3. Convolve with a circular disk kernel via FFT  (O(R·C·log(R·C)))
    This replaces 14,760 × circle-queries with one FFT pass.

  - Transit uses a KD-tree on ~472 subway stations, with a single
    vectorized query_ball_point for all 14,760 grid cells.

  - Parks iterates over ~2,000 park polygons, building a mini KD-tree
    per park for its boundary vertices, then querying all grid cells at
    once (vectorized nearest-neighbor).

  - Pest combines FFT convolution (311 rodent complaints) with a
    KD-tree nearest-neighbour join (HPD complaints via PLUTO BBL lookup).
"""

from __future__ import annotations

import json
import math
import os
import sys
import time

import numpy as np
from scipy.signal import fftconvolve
from scipy.spatial import cKDTree
from scipy.stats import rankdata

# ── Path setup ───────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt.db import get_connection
from apthunt.data.data_store import DataStore
from apthunt.data.transit_data import TransitData

# ── Grid parameters ──────────────────────────────────────────────

MIN_LAT, MAX_LAT = 40.49, 40.92
MIN_LNG, MAX_LNG = -74.27, -73.68

LAT_STEP = 0.0036   # ~400 m north–south
LNG_STEP = 0.0048   # ~400 m east–west at NYC latitude

# Derived constants
LAT_M  = 111_320.0          # metres per degree latitude
LNG_M  =  85_000.0          # metres per degree longitude at ~40.7°
CELL_H = LAT_STEP * LAT_M   # ~400.8 m per row
CELL_W = LNG_STEP * LNG_M   # ~408.0 m per col

ROWS = int((MAX_LAT - MIN_LAT) / LAT_STEP) + 1   # 120
COLS = int((MAX_LNG - MIN_LNG) / LNG_STEP) + 1    # 123

OUT_DIR = os.path.join(ROOT, "frontend", "public", "heatmap")


# ── Utility functions ────────────────────────────────────────────

def make_disk_kernel(radius_m: float) -> np.ndarray:
    """Binary disk kernel where each pixel covers CELL_H × CELL_W metres."""
    r_r = max(1, int(np.ceil(radius_m / CELL_H)))
    r_c = max(1, int(np.ceil(radius_m / CELL_W)))
    Y, X = np.mgrid[-r_r:r_r + 1, -r_c:r_c + 1]
    dist = np.sqrt((Y * CELL_H) ** 2 + (X * CELL_W) ** 2)
    return (dist <= radius_m).astype(np.float64)


def bin_points(
    lats: np.ndarray,
    lons: np.ndarray,
    weights: np.ndarray | None = None,
) -> np.ndarray:
    """Bin lat/lon points into the grid.  Returns (ROWS, COLS) float64."""
    ri = np.rint((MAX_LAT - lats) / LAT_STEP).astype(np.intp)
    ci = np.rint((lons - MIN_LNG) / LNG_STEP).astype(np.intp)
    valid = (ri >= 0) & (ri < ROWS) & (ci >= 0) & (ci < COLS)
    grid = np.zeros((ROWS, COLS), dtype=np.float64)
    if weights is not None:
        np.add.at(grid, (ri[valid], ci[valid]), weights[valid])
    else:
        np.add.at(grid, (ri[valid], ci[valid]), 1.0)
    return grid


def disk_sum(grid: np.ndarray, radius_m: float) -> np.ndarray:
    """Convolve grid with a disk kernel → local sum within radius."""
    kernel = make_disk_kernel(radius_m)
    result = fftconvolve(grid, kernel, mode="same")
    np.clip(result, 0, None, out=result)
    return result


def grid_coords_m() -> np.ndarray:
    """Return (ROWS*COLS, 2) array of grid-cell centres in pseudo-metres."""
    lats = MAX_LAT - np.arange(ROWS) * LAT_STEP
    lons = MIN_LNG + np.arange(COLS) * LNG_STEP
    lat2d, lon2d = np.meshgrid(lats, lons, indexing="ij")
    return np.column_stack((lat2d.ravel() * LAT_M, lon2d.ravel() * LNG_M))


def percentile_rank(
    grid: np.ndarray,
    land: np.ndarray,
    *,
    reverse: bool = False,
) -> np.ndarray:
    """Percentile-rank land cells (0–100).  NaN for water/void."""
    vals = grid[land]
    n = len(vals)
    if n == 0:
        return np.full_like(grid, np.nan)
    if n == 1:
        out = np.full_like(grid, np.nan)
        out[land] = 50.0
        return out

    ranks = rankdata(vals, method="average")      # 1-based, tie-averaged
    pct = (ranks - 1) / (n - 1) * 100.0
    if reverse:
        pct = 100.0 - pct
    pct = np.round(pct, 1)

    out = np.full_like(grid, np.nan)
    out[land] = pct
    return out


def save_grid(name: str, grid: np.ndarray) -> None:
    """Write grid as compact JSON (NaN → null)."""
    scores = []
    for r in range(ROWS):
        row_data = []
        for c in range(COLS):
            v = grid[r, c]
            row_data.append(round(float(v), 1) if not np.isnan(v) else None)
        scores.append(row_data)

    data = {
        "bounds": {
            "minLat": MIN_LAT, "maxLat": MAX_LAT,
            "minLng": MIN_LNG, "maxLng": MAX_LNG,
        },
        "rows": ROWS,
        "cols": COLS,
        "latStep": LAT_STEP,
        "lngStep": LNG_STEP,
        "scores": scores,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"  → {path} ({os.path.getsize(path) / 1024:.1f} KB)")


def print_dist(grid: np.ndarray, land: np.ndarray) -> None:
    vals = np.sort(grid[land & ~np.isnan(grid)])
    n = len(vals)
    if not n:
        return
    print(f"  p5={vals[int(n*0.05)]:.0f}  p25={vals[int(n*0.25)]:.0f}  "
          f"p50={vals[n//2]:.0f}  p75={vals[int(n*0.75)]:.0f}  "
          f"p95={vals[int(n*0.95)]:.0f}")


# ── Load helpers ─────────────────────────────────────────────────

def _safe_floats(rows, lat_col, lon_col):
    """Extract valid (lat, lon) arrays from fetched rows."""
    lats, lons = [], []
    for r in rows:
        try:
            la = float(r[lat_col])
            lo = float(r[lon_col])
        except (TypeError, ValueError):
            continue
        if la == 0 or lo == 0:
            continue
        lats.append(la)
        lons.append(lo)
    return np.array(lats, dtype=np.float64), np.array(lons, dtype=np.float64)


# ── Scorer functions ─────────────────────────────────────────────

def compute_crime(conn) -> np.ndarray:
    """Crime weighted total via 2D convolution.  Radius: 400 m."""
    RADIUS = 400
    WEIGHTS = {"FELONY": 3.0, "MISDEMEANOR": 1.5, "VIOLATION": 1.0}

    rows = conn.execute(
        "SELECT latitude, longitude, law_cat_cd FROM ds_crime "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()

    lats, lons, weights = [], [], []
    for r in rows:
        try:
            la = float(r["latitude"])
            lo = float(r["longitude"])
        except (TypeError, ValueError):
            continue
        if la == 0 or lo == 0:
            continue
        cat = (r["law_cat_cd"] or "").upper()
        w = WEIGHTS.get(cat, 0.0)
        if w == 0:
            continue
        lats.append(la)
        lons.append(lo)
        weights.append(w)

    lats = np.array(lats, dtype=np.float64)
    lons = np.array(lons, dtype=np.float64)
    weights = np.array(weights, dtype=np.float64)

    weighted_grid = bin_points(lats, lons, weights)
    return disk_sum(weighted_grid, RADIUS)


def compute_noise(conn) -> np.ndarray:
    """Noise complaint count via 2D convolution.  Radius: 300 m."""
    RADIUS = 300
    NOISE_TYPES = {
        "Noise - Residential", "Noise - Street/Sidewalk",
        "Noise - Commercial", "Noise - Vehicle", "Noise - Park",
    }

    rows = conn.execute(
        "SELECT latitude, longitude, complaint_type FROM ds_noise "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()

    lats, lons = [], []
    for r in rows:
        ct = r["complaint_type"] or ""
        if ct not in NOISE_TYPES:
            continue
        try:
            la = float(r["latitude"])
            lo = float(r["longitude"])
        except (TypeError, ValueError):
            continue
        if la == 0 or lo == 0:
            continue
        lats.append(la)
        lons.append(lo)

    lats = np.array(lats, dtype=np.float64)
    lons = np.array(lons, dtype=np.float64)

    count_grid = bin_points(lats, lons)
    return disk_sum(count_grid, RADIUS)


def compute_transit(grid_m: np.ndarray) -> np.ndarray:
    """Transit score via KD-tree on subway stations.  Radius: 800 m."""
    RADIUS = 800
    stops_path = os.path.join(ROOT, "data", "stops.txt")
    td = TransitData(stops_path)
    stations = td._stations

    if not stations:
        return np.zeros((ROWS, COLS))

    s_lats = np.array([s.lat for s in stations])
    s_lons = np.array([s.lon for s in stations])
    station_m = np.column_stack((s_lats * LAT_M, s_lons * LNG_M))

    tree = cKDTree(station_m)
    neighbours = tree.query_ball_point(grid_m, r=RADIUS)

    scores = np.zeros(ROWS * COLS)
    for i, nbrs in enumerate(neighbours):
        if not nbrs:
            continue
        station_count = len(nbrs)
        routes: set[str] = set()
        for idx in nbrs:
            routes.update(stations[idx].routes)
        raw = station_count * 12 + len(routes) * 3
        scores[i] = min(100.0, float(raw))

    return scores.reshape(ROWS, COLS)


def _points_in_polygon(pts_xy: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Vectorized ray-casting point-in-polygon test.

    pts_xy : (N, 2) test points  [x, y]  (lon, lat)
    ring   : (M, 2) polygon ring [x, y]  (lon, lat)
    Returns: (N,) bool array — True if point is inside the ring.
    """
    px, py = pts_xy[:, 0], pts_xy[:, 1]
    n = len(ring)
    inside = np.zeros(len(pts_xy), dtype=bool)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i, 0], ring[i, 1]
        xj, yj = ring[j, 0], ring[j, 1]
        if yi != yj:  # skip horizontal edges
            cond = ((yi > py) != (yj > py)) & (
                px < (xj - xi) * (py - yi) / (yj - yi) + xi
            )
            inside ^= cond
        j = i
    return inside


def _extract_outer_rings(geom: dict) -> list[np.ndarray]:
    """Return outer rings of a GeoJSON Polygon/MultiPolygon as numpy arrays."""
    gtype = geom.get("type", "")
    raw = geom.get("coordinates", [])
    rings = []
    if gtype == "MultiPolygon":
        for poly in raw:
            if poly and len(poly[0]) >= 3:
                rings.append(np.array(poly[0], dtype=np.float64))
    elif gtype == "Polygon":
        if raw and len(raw[0]) >= 3:
            rings.append(np.array(raw[0], dtype=np.float64))
    return rings


def compute_parks(conn, grid_m: np.ndarray) -> np.ndarray:
    """Parks score: max(proximity × quality × 100) across all nearby parks.

    Uses point-in-polygon to give park interiors max score, plus KD-tree
    proximity decay for cells outside the boundary but within reach.
    """
    TIERS = [
        (100.0, 1.00, 2000),
        ( 15.0, 0.95, 1200),
        (  3.0, 0.85,  800),
        (  0.5, 0.70,  500),
        (  0.0, 0.55,  300),
    ]

    park_rows = conn.execute(
        "SELECT name311, multipolygon FROM ds_parks"
    ).fetchall()

    best = np.zeros(ROWS * COLS, dtype=np.float64)

    # Build grid lon/lat array for point-in-polygon tests (computed once)
    g_lats = MAX_LAT - np.arange(ROWS) * LAT_STEP
    g_lons = MIN_LNG + np.arange(COLS) * LNG_STEP
    lat2d, lon2d = np.meshgrid(g_lats, g_lons, indexing="ij")
    grid_lonlat = np.column_stack((lon2d.ravel(), lat2d.ravel()))  # (N, 2)

    for row in park_rows:
        mp_raw = row["multipolygon"]
        if not mp_raw:
            continue
        try:
            geom = json.loads(mp_raw) if isinstance(mp_raw, str) else mp_raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(geom, dict):
            continue

        vertices = _extract_coords(geom)
        if len(vertices) < 3:
            continue

        acres = _polygon_area_acres(geom)

        # Determine tier
        quality, reach = 0.55, 300
        for min_ac, q, r in TIERS:
            if acres >= min_ac:
                quality, reach = q, r
                break

        # Quick centroid check — skip parks far outside the grid
        v_arr = np.array(vertices, dtype=np.float64)   # (V, 2) [lon, lat]
        v_lat, v_lon = v_arr[:, 1], v_arr[:, 0]
        clat, clon = v_lat.mean(), v_lon.mean()
        margin = reach / LAT_M * 1.5
        if (clat < MIN_LAT - margin or clat > MAX_LAT + margin or
                clon < MIN_LNG - margin or clon > MAX_LNG + margin):
            continue

        # Build mini KD-tree from this park's DENSIFIED vertices
        # Densifying ensures the nearest-vertex distance closely matches
        # the true nearest-edge distance (fixes cells near straight edges).
        densified = _densify_coords(vertices, max_spacing_m=50.0)
        d_arr = np.array(densified, dtype=np.float64)
        d_lat, d_lon = d_arr[:, 1], d_arr[:, 0]
        v_m = np.column_stack((d_lat * LAT_M, d_lon * LNG_M))
        tree = cKDTree(v_m)

        # Nearest-vertex distance for ALL grid cells at once
        dists, _ = tree.query(grid_m)

        within = dists < reach
        # Quadratic decay for cells outside boundary but within reach
        proximity = np.where(within, 1.0 - (dists / reach) ** 2, 0.0)

        # Point-in-polygon: cells INSIDE the park get proximity = 1.0
        outer_rings = _extract_outer_rings(geom)
        for ring in outer_rings:
            interior = _points_in_polygon(grid_lonlat, ring)
            proximity = np.where(interior, 1.0, proximity)

        effective = proximity * quality * 100.0
        np.maximum(best, effective, out=best)

    return best.reshape(ROWS, COLS)


def compute_greenery(conn) -> np.ndarray:
    """Greenery absolute score via 2D convolution.

    Combines street-tree count/canopy (r=200 m) and community gardens
    (r=500 m) using the same sqrt diminishing-returns formula as
    GreeneryScorer._absolute_score.
    """
    TREE_RADIUS = 200
    GARDEN_RADIUS = 500
    TREE_CAP = 200
    CANOPY_CAP = 2000
    GARDEN_CAP = 3

    # ── Street trees ─────────────────────────────────────────────
    tree_rows = conn.execute(
        "SELECT latitude, longitude, tree_dbh FROM ds_street_trees "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()

    t_lats, t_lons, t_dbh = [], [], []
    for r in tree_rows:
        try:
            la = float(r["latitude"])
            lo = float(r["longitude"])
            dbh = min(int(float(r["tree_dbh"] or 0)), 36)
        except (TypeError, ValueError):
            continue
        if la == 0 or lo == 0:
            continue
        t_lats.append(la)
        t_lons.append(lo)
        t_dbh.append(dbh)

    t_lats = np.array(t_lats, dtype=np.float64)
    t_lons = np.array(t_lons, dtype=np.float64)
    t_dbh  = np.array(t_dbh,  dtype=np.float64)

    tree_count = disk_sum(bin_points(t_lats, t_lons), TREE_RADIUS)
    canopy_sum = disk_sum(bin_points(t_lats, t_lons, t_dbh), TREE_RADIUS)

    # ── Community gardens ────────────────────────────────────────
    g_rows = conn.execute(
        "SELECT latitude, longitude FROM ds_community_gardens "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()

    g_lats, g_lons = _safe_floats(g_rows, "latitude", "longitude")

    garden_count = disk_sum(bin_points(g_lats, g_lons), GARDEN_RADIUS)

    # ── Absolute score formula ───────────────────────────────────
    tree_pts   = np.sqrt(np.minimum(1.0, tree_count  / TREE_CAP))  * 40
    canopy_pts = np.sqrt(np.minimum(1.0, canopy_sum  / CANOPY_CAP)) * 40
    garden_pts = np.sqrt(np.minimum(1.0, garden_count / GARDEN_CAP)) * 20

    return np.minimum(tree_pts + canopy_pts + garden_pts, 100.0)


def compute_convenience(conn) -> np.ndarray:
    """Total amenity count via 2D convolution.  Radius: 500 m."""
    RADIUS = 500

    rows = conn.execute(
        "SELECT lat, lon FROM ds_amenities "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()

    lats, lons = _safe_floats(rows, "lat", "lon")
    count_grid = bin_points(lats, lons)
    return disk_sum(count_grid, RADIUS)


def compute_pest(conn, grid_m: np.ndarray) -> np.ndarray:
    """Pest total = HPD building pests (KD-tree) + 311 rodents (convolution).

    HPD pests: count complaints per BBL, join with PLUTO lat/lon, then
    nearest-neighbour lookup for each grid cell.
    Rodents: bin to grid and convolve with 100 m disk.
    """
    RODENT_RADIUS = 100

    # ── Part 1: 311 rodent complaints ────────────────────────────
    rodent_rows = conn.execute(
        "SELECT latitude, longitude FROM ds_noise "
        "WHERE complaint_type = 'Rodent' "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()

    r_lats, r_lons = _safe_floats(rodent_rows, "latitude", "longitude")
    rodent_grid = disk_sum(bin_points(r_lats, r_lons), RODENT_RADIUS)

    # ── Part 2: HPD pest complaints via PLUTO BBL join ───────────
    # Count HPD pest complaints per BBL
    hpd_rows = conn.execute(
        "SELECT bbl, COUNT(*) AS cnt FROM ds_hpd_complaints "
        "WHERE major_category = 'UNSANITARY CONDITION' "
        "AND minor_category = 'PESTS' "
        "GROUP BY bbl"
    ).fetchall()

    bbl_count: dict[str, int] = {}
    for r in hpd_rows:
        try:
            bbl = str(int(float(r["bbl"])))
        except (TypeError, ValueError):
            bbl = str(r["bbl"])
        bbl_count[bbl] = int(r["cnt"])

    # Load PLUTO buildings — attach pest count per BBL
    pluto_rows = conn.execute(
        "SELECT latitude, longitude, bbl FROM ds_pluto "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()

    p_lats, p_lons, p_pest = [], [], []
    for r in pluto_rows:
        try:
            la = float(r["latitude"])
            lo = float(r["longitude"])
        except (TypeError, ValueError):
            continue
        if la == 0 or lo == 0:
            continue
        try:
            bbl = str(int(float(r["bbl"])))
        except (TypeError, ValueError):
            bbl = str(r["bbl"])
        p_lats.append(la)
        p_lons.append(lo)
        p_pest.append(bbl_count.get(bbl, 0))

    p_lats = np.array(p_lats, dtype=np.float64)
    p_lons = np.array(p_lons, dtype=np.float64)
    p_pest = np.array(p_pest, dtype=np.float64)

    hpd_grid = np.zeros((ROWS, COLS))
    if len(p_lats) > 0:
        pluto_m = np.column_stack((p_lats * LAT_M, p_lons * LNG_M))
        pluto_tree = cKDTree(pluto_m)
        dists, idx = pluto_tree.query(grid_m)
        hpd_flat = np.where(dists < 500, p_pest[idx], 0)   # cap at 500 m
        hpd_grid = hpd_flat.reshape(ROWS, COLS)

    return hpd_grid + rodent_grid


# ── Park geometry helpers ────────────────────────────────────────

def _densify_coords(vertices: list[list[float]], max_spacing_m: float = 50.0) -> list[list[float]]:
    """Interpolate extra vertices so no edge is longer than max_spacing_m.

    This ensures the KD-tree distance to nearest vertex closely
    approximates the true distance to the nearest edge — critical for
    cells near straight polygon edges (e.g. Central Park's east side).
    """
    result: list[list[float]] = []
    n = len(vertices)
    for i in range(n):
        p1 = vertices[i]
        p2 = vertices[(i + 1) % n]
        result.append(p1)
        dx = (p2[0] - p1[0]) * LNG_M
        dy = (p2[1] - p1[1]) * LAT_M
        edge_len = math.sqrt(dx * dx + dy * dy)
        if edge_len > max_spacing_m:
            n_seg = int(math.ceil(edge_len / max_spacing_m))
            for j in range(1, n_seg):
                t = j / n_seg
                result.append([
                    p1[0] + (p2[0] - p1[0]) * t,
                    p1[1] + (p2[1] - p1[1]) * t,
                ])
    return result


def _extract_coords(geom: dict) -> list[list[float]]:
    """All [lon, lat] vertices from a GeoJSON Polygon / MultiPolygon."""
    gtype = geom.get("type", "")
    raw = geom.get("coordinates", [])
    pts: list[list[float]] = []
    if gtype == "MultiPolygon":
        for poly in raw:
            for ring in poly:
                pts.extend(ring)
    elif gtype == "Polygon":
        for ring in raw:
            pts.extend(ring)
    return pts


def _polygon_area_acres(geom: dict) -> float:
    """Approximate acreage via Shoelace formula."""
    gtype = geom.get("type", "")
    raw = geom.get("coordinates", [])
    total = 0.0
    if gtype == "MultiPolygon":
        for poly in raw:
            if poly:
                total += _ring_area(poly[0])
    elif gtype == "Polygon":
        if raw:
            total += _ring_area(raw[0])
    return total * 0.000247105


def _ring_area(ring: list) -> float:
    """Shoelace area in m² for a ring of [lon, lat] pairs."""
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


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("Heatmap grid generator (vectorized — numpy/scipy)")
    print("=" * 60)

    conn = get_connection()
    store = DataStore(conn)

    # Ensure all datasets are downloaded (no-op if already cached)
    for ds in ("crime", "noise", "street_trees", "community_gardens",
               "parks", "amenities", "pluto", "hpd_complaints"):
        store.ensure_downloaded(ds, quiet=True)

    print(f"Grid: {ROWS} rows × {COLS} cols = {ROWS * COLS:,} cells")
    print(f"Resolution: ~{CELL_H:.0f} m × ~{CELL_W:.0f} m")
    print()

    grid_m = grid_coords_m()
    t_total = time.time()

    # ── Step 1: Compute raw grids ────────────────────────────────

    t0 = time.time()
    crime_raw = compute_crime(conn)
    print(f"[crime]       {time.time() - t0:.1f}s  "
          f"(max weighted total: {crime_raw.max():.0f})")

    t0 = time.time()
    noise_raw = compute_noise(conn)
    print(f"[noise]       {time.time() - t0:.1f}s  "
          f"(max complaints: {noise_raw.max():.0f})")

    t0 = time.time()
    transit_raw = compute_transit(grid_m)
    print(f"[transit]     {time.time() - t0:.1f}s  "
          f"(max score: {transit_raw.max():.0f})")

    t0 = time.time()
    parks_raw = compute_parks(conn, grid_m)
    print(f"[parks]       {time.time() - t0:.1f}s  "
          f"(max effective: {parks_raw.max():.1f})")

    t0 = time.time()
    greenery_raw = compute_greenery(conn)
    print(f"[greenery]    {time.time() - t0:.1f}s  "
          f"(max abs score: {greenery_raw.max():.1f})")

    t0 = time.time()
    convenience_raw = compute_convenience(conn)
    print(f"[convenience] {time.time() - t0:.1f}s  "
          f"(max amenities: {convenience_raw.max():.0f})")

    t0 = time.time()
    pest_raw = compute_pest(conn, grid_m)
    print(f"[pest]        {time.time() - t0:.1f}s  "
          f"(max total: {pest_raw.max():.0f})")

    # ── Step 2: Water mask ───────────────────────────────────────
    # A cell is water/void if ALL of:
    #   - greenery ≈ 0 (no trees/canopy/gardens)
    #   - convenience ≈ 0 (no amenities)
    #   - parks score < 20 (not inside or near a park)
    water = (greenery_raw < 0.01) & (convenience_raw < 0.01) & (parks_raw < 20)
    land = ~water

    print(f"\nWater mask: {water.sum():,} water, {land.sum():,} land")

    # ── Step 3: Percentile-rank and save ─────────────────────────

    # Crime (reverse: lower = better)
    print("\n[crime] Percentile-ranking (reverse)...")
    crime_pct = percentile_rank(crime_raw, land, reverse=True)
    save_grid("crime", crime_pct)
    print_dist(crime_pct, land)

    # Noise (reverse: lower = better)
    print("\n[noise] Percentile-ranking (reverse)...")
    noise_pct = percentile_rank(noise_raw, land, reverse=True)
    save_grid("noise", noise_pct)
    print_dist(noise_pct, land)

    # Transit (direct: higher = better)
    print("\n[transit] Percentile-ranking...")
    transit_pct = percentile_rank(transit_raw, land)
    save_grid("transit", transit_pct)
    print_dist(transit_pct, land)

    # Green space: max(parks, greenery) blend
    print("\n[green_space] max(parks, greenery) blend...")
    green_blend = np.maximum(parks_raw, greenery_raw)
    secondary = np.minimum(parks_raw, greenery_raw)
    bonus = np.minimum(10.0, secondary * 0.15)
    green_space = np.minimum(100.0, green_blend + bonus)
    gs_pct = percentile_rank(green_space, land)
    save_grid("green_space", gs_pct)
    print_dist(gs_pct, land)

    # Convenience (direct: higher = better)
    print("\n[convenience] Percentile-ranking...")
    conv_pct = percentile_rank(convenience_raw, land)
    save_grid("convenience", conv_pct)
    print_dist(conv_pct, land)

    # Pest (reverse: lower = better)
    print("\n[pest] Percentile-ranking (reverse)...")
    pest_pct = percentile_rank(pest_raw, land, reverse=True)
    save_grid("pest", pest_pct)
    print_dist(pest_pct, land)

    conn.close()
    elapsed = time.time() - t_total
    print(f"\nDone! 6 heatmap grids written to {OUT_DIR}")
    print(f"Total time: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
