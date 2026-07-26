#!/usr/bin/env python3
"""
Vectorized heatmap grid generator — residential-rate edition.

Loads each dataset ONCE into numpy arrays and uses 2D FFT convolution +
KD-trees, so the full grid builds in tens of seconds.

Statistical model (matches the per-listing scorers)
---------------------------------------------------
Incident layers (crime / noise / pest) are **rates, not counts**:

    rate = (recency-decayed, severity-weighted incidents near cell)
           / max(residential units near cell, 50)

  - "near" = the scorers' Gaussian kernel exp(-(d/sigma)^2) with
    sigma = radius/2, truncated at radius — the same kernel for
    numerator and denominator, so the population-density confound
    cancels.
  - Every incident is weighted by 0.5 ** (age_days / 180) — the same
    half-life as apthunt.scoring.baseline.decay_weight.
  - Crime additionally weights FELONY 3.0 / MISDEMEANOR 1.5 /
    VIOLATION 1.0 (law_cat_cd), like CrimeScorer.
  - 311 streams (noise, rodents) are winsorized per exact point:
    chronic repeat callers and geocoding collapse points (worst: one
    point carrying 71,629 noise complaints) are capped so no single
    address dominates its neighbourhood.

Cells with fewer than 50 residential units in the kernel neighbourhood
(PLUTO ``unitsres``) are non-residential — water, parks, industrial,
airports, out-of-city — and are written as ``null`` (uncolored).

Percentile basis: every layer is percentile-ranked ONLY across
residential cells, which makes the residential score distribution
uniform by construction — a full red→green gradient exactly where
people live.  Incident layers are inverted (higher score = safer /
quieter = greener).

Transit is a continuous metric — 100 * exp(-nearest_subway_entrance_m
/ 400) from exact entrance points (ds_subway_entrances) plus a small
capped bus-route bonus (ds_bus_stops) — so there is no giant tie block.

Output format (unchanged — the frontend depends on it):
    frontend/public/heatmap/{crime,noise,transit,green_space,
                             convenience,pest}.json
    {bounds, rows, cols, latStep, lngStep, scores}
    scores[0] is the NORTHERNMOST row (row index grows southward);
    null = uncolored cell.
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
from apthunt.scoring.baseline import DECAY_HALF_LIFE_DAYS

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

# Minimum Gaussian-weighted residential units for a cell to count as
# residential (and be colored at all).
RES_UNITS_MIN = 50.0


# ── Kernels ──────────────────────────────────────────────────────

def gaussian_kernel(radius_m: float, *, norm: str = "mean",
                    oversample: int = 8) -> np.ndarray:
    """Truncated-Gaussian kernel matching the listing scorers.

    Weight = exp(-(d/sigma)^2) with sigma = radius/2, zero beyond
    radius — the EXACT kernel used by NoiseScorer / CrimeScorer /
    kernel_weighted_units (see apthunt/scoring/noise.py), so heatmap
    rates and per-listing rates share one definition.

    The grid cells (~400 m) are coarse relative to some radii, so each
    kernel pixel is super-sampled ``oversample × oversample`` and the
    truncated Gaussian is averaged over the pixel area.  Without this, a
    100–400 m kernel would collapse to a single pixel (no neighbour cell
    CENTER is within radius, even though parts of neighbour cells are).

    norm="mean": kernel sums to 1 → convolution gives the Gaussian-
        weighted neighbourhood MEAN per cell.  Used for incident rates:
        numerator and denominator use the identical kernel, and the
        units threshold keeps a stable "units per cell" scale.
    norm="area": kernel sums to the disk area in pixels → convolution
        approximates the classic "count within radius", keeping legacy
        cap constants (trees/gardens) meaningful.
    """
    sigma = radius_m / 2.0
    r_r = max(1, int(np.ceil((radius_m + CELL_H / 2) / CELL_H)))
    r_c = max(1, int(np.ceil((radius_m + CELL_W / 2) / CELL_W)))
    sub = (np.arange(oversample) + 0.5) / oversample - 0.5

    off_y = ((np.arange(2 * r_r + 1) - r_r)[:, None] + sub[None, :]).ravel()
    off_x = ((np.arange(2 * r_c + 1) - r_c)[:, None] + sub[None, :]).ravel()
    y_m = off_y * CELL_H
    x_m = off_x * CELL_W
    d2 = y_m[:, None] ** 2 + x_m[None, :] ** 2

    inside = d2 <= radius_m ** 2
    w_fine = np.where(inside, np.exp(-d2 / sigma ** 2), 0.0)
    kernel = w_fine.reshape(2 * r_r + 1, oversample,
                            2 * r_c + 1, oversample).mean(axis=(1, 3))
    coverage = inside.astype(np.float64).reshape(
        2 * r_r + 1, oversample, 2 * r_c + 1, oversample).mean(axis=(1, 3))

    # Trim all-zero border rows/cols
    nz_r = np.flatnonzero(kernel.sum(axis=1) > 0)
    nz_c = np.flatnonzero(kernel.sum(axis=0) > 0)
    kernel = kernel[nz_r[0]:nz_r[-1] + 1, nz_c[0]:nz_c[-1] + 1]
    coverage = coverage[nz_r[0]:nz_r[-1] + 1, nz_c[0]:nz_c[-1] + 1]

    if norm == "mean":
        kernel /= kernel.sum()
    elif norm == "area":
        kernel *= coverage.sum() / kernel.sum()
    else:
        raise ValueError(f"unknown kernel norm: {norm}")
    return kernel


def conv(grid: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """FFT convolution, clipped at 0 (removes FFT ringing)."""
    result = fftconvolve(grid, kernel, mode="same")
    np.clip(result, 0, None, out=result)
    return result


# ── Utility functions ────────────────────────────────────────────

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


def grid_coords_m() -> np.ndarray:
    """Return (ROWS*COLS, 2) array of grid-cell centres in pseudo-metres."""
    lats = MAX_LAT - np.arange(ROWS) * LAT_STEP
    lons = MIN_LNG + np.arange(COLS) * LNG_STEP
    lat2d, lon2d = np.meshgrid(lats, lons, indexing="ij")
    return np.column_stack((lat2d.ravel() * LAT_M, lon2d.ravel() * LNG_M))


def winsorize_by_point(
    lats: np.ndarray,
    lons: np.ndarray,
    weights: np.ndarray,
    cap: float,
) -> np.ndarray:
    """Cap the total weight contributed by any single exact coordinate.

    311 data is polluted by chronic repeat callers and geocoding
    collapse points (worst offender: one Woodlawn-area point carrying
    71,629 noise complaints — >2000× the p95 point).  Uncapped, one
    such point paints its whole neighbourhood red regardless of actual
    conditions.  Weights are scaled per point so each point's total
    decayed weight is at most ``cap`` (≈ a dozen recent complaints).
    """
    key = lats + 1j * lons          # exact-precision coordinate pairing
    uniq, inv = np.unique(key, return_inverse=True)
    totals = np.bincount(inv, weights=weights)
    scale = np.minimum(totals, cap) / np.maximum(totals, 1e-12)
    return weights * scale[inv]


def decay_weights(date_strs: list) -> np.ndarray:
    """Vectorized recency weights: 0.5 ** (age_days / 180).

    Same half-life as apthunt.scoring.baseline.decay_weight.
    Unparseable / missing dates get 0.5 (present, age unknown).
    """
    today = np.datetime64("today", "D")
    clean = np.array(
        ["NaT" if not s else str(s)[:10] for s in date_strs], dtype="U10"
    )
    try:
        dates = clean.astype("datetime64[D]")
    except ValueError:
        def _one(s: str):
            try:
                return np.datetime64(s, "D")
            except ValueError:
                return np.datetime64("NaT")
        dates = np.array([_one(s) for s in clean], dtype="datetime64[D]")

    age = (today - dates) / np.timedelta64(1, "D")   # float; NaT → nan
    age = np.clip(age, 0.0, None)                    # future dates → 0
    with np.errstate(invalid="ignore"):
        w = 0.5 ** (age / DECAY_HALF_LIFE_DAYS)
    return np.where(np.isnan(w), 0.5, w)


def percentile_rank(
    grid: np.ndarray,
    mask: np.ndarray,
    *,
    reverse: bool = False,
) -> np.ndarray:
    """Percentile-rank cells inside ``mask`` (0–100).  NaN elsewhere.

    Ranking only over residential cells makes their distribution uniform
    by construction.  ``reverse=True`` inverts (low raw = high score) —
    used for incident rates so green = safe/quiet.
    """
    vals = grid[mask]
    n = len(vals)
    if n == 0:
        return np.full_like(grid, np.nan)
    if n == 1:
        out = np.full_like(grid, np.nan)
        out[mask] = 50.0
        return out

    ranks = rankdata(vals, method="average")      # 1-based, tie-averaged
    pct = (ranks - 1) / (n - 1) * 100.0
    if reverse:
        pct = 100.0 - pct
    pct = np.round(pct, 1)

    out = np.full_like(grid, np.nan)
    out[mask] = pct
    return out


def save_grid(name: str, grid: np.ndarray) -> None:
    """Write grid as compact JSON (NaN → null).

    Format contract with the frontend: {bounds, rows, cols, latStep,
    lngStep, scores}; scores[0] = northernmost row (maxLat), row index
    increases southward.
    """
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


def load_pluto(conn):
    """Residential-units grid + BBL → (lat, lon) map from PLUTO.

    Returns:
        units_grid: (ROWS, COLS) sum of ``unitsres`` binned per cell —
            the denominator source for all incident-rate layers.
        bbl_coords: normalized-BBL → (lat, lon) for joining HPD
            complaints (which carry BBL but no coordinates).
    """
    rows = conn.execute(
        "SELECT latitude, longitude, unitsres, bbl FROM ds_pluto "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()

    lats, lons, units = [], [], []
    bbl_coords: dict[str, tuple[float, float]] = {}
    for r in rows:
        try:
            la = float(r["latitude"])
            lo = float(r["longitude"])
        except (TypeError, ValueError):
            continue
        if la == 0 or lo == 0:
            continue
        try:
            u = float(r["unitsres"] or 0)
        except (TypeError, ValueError):
            u = 0.0
        try:
            bbl = str(int(float(r["bbl"])))
        except (TypeError, ValueError):
            bbl = str(r["bbl"])
        bbl_coords[bbl] = (la, lo)
        if u > 0:
            lats.append(la)
            lons.append(lo)
            units.append(u)

    units_grid = bin_points(
        np.array(lats, dtype=np.float64),
        np.array(lons, dtype=np.float64),
        np.array(units, dtype=np.float64),
    )
    return units_grid, bbl_coords


# ── Incident numerators (binned, decay-weighted point grids) ─────

def crime_points(conn) -> np.ndarray:
    """Severity × recency weighted crime incidents, binned to the grid."""
    SEVERITY = {"FELONY": 3.0, "MISDEMEANOR": 1.5, "VIOLATION": 1.0}

    rows = conn.execute(
        "SELECT latitude, longitude, law_cat_cd, cmplnt_fr_dt FROM ds_crime "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()

    lats, lons, sev, dates = [], [], [], []
    for r in rows:
        try:
            la = float(r["latitude"])
            lo = float(r["longitude"])
        except (TypeError, ValueError):
            continue
        if la == 0 or lo == 0:
            continue
        w = SEVERITY.get((r["law_cat_cd"] or "").upper(), 0.0)
        if w == 0:
            continue
        lats.append(la)
        lons.append(lo)
        sev.append(w)
        dates.append(r["cmplnt_fr_dt"])

    weights = np.array(sev, dtype=np.float64) * decay_weights(dates)
    return bin_points(np.array(lats, dtype=np.float64),
                      np.array(lons, dtype=np.float64), weights)


def noise_points(conn) -> np.ndarray:
    """Recency-weighted noise complaints, winsorized and binned.

    Per-point winsorization (POINT_CAP) guards against chronic repeat
    callers and geocoding collapse points — see winsorize_by_point.
    """
    NOISE_TYPES = (
        "Noise - Residential", "Noise - Street/Sidewalk",
        "Noise - Commercial", "Noise - Vehicle", "Noise - Park",
    )
    POINT_CAP = 5.0
    placeholders = ",".join("?" * len(NOISE_TYPES))
    rows = conn.execute(
        f"SELECT latitude, longitude, created_date FROM ds_noise "
        f"WHERE complaint_type IN ({placeholders}) "
        f"AND latitude IS NOT NULL AND longitude IS NOT NULL",
        NOISE_TYPES,
    ).fetchall()

    lats, lons, dates = [], [], []
    for r in rows:
        try:
            la = float(r["latitude"])
            lo = float(r["longitude"])
        except (TypeError, ValueError):
            continue
        if la == 0 or lo == 0:
            continue
        lats.append(la)
        lons.append(lo)
        dates.append(r["created_date"])

    lats = np.array(lats, dtype=np.float64)
    lons = np.array(lons, dtype=np.float64)
    w = winsorize_by_point(lats, lons, decay_weights(dates), POINT_CAP)
    return bin_points(lats, lons, w)


def pest_points(conn, bbl_coords: dict) -> np.ndarray:
    """Recency-weighted pest signal: 311 rodents + HPD pest complaints.

    311 rodent complaints carry coordinates; HPD pest complaints carry a
    BBL that is joined to PLUTO building coordinates.  Both streams are
    decay-weighted and binned into one point grid, then (in main) share
    the 100 m Gaussian kernel and the residential-units denominator.
    The 311 stream is winsorized per point (chronic-caller guard); HPD
    complaints are building-level and stay uncapped.
    """
    POINT_CAP = 5.0

    r_lats, r_lons, r_dates = [], [], []
    rodent_rows = conn.execute(
        "SELECT latitude, longitude, created_date FROM ds_noise "
        "WHERE complaint_type = 'Rodent' "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()
    for r in rodent_rows:
        try:
            la = float(r["latitude"])
            lo = float(r["longitude"])
        except (TypeError, ValueError):
            continue
        if la == 0 or lo == 0:
            continue
        r_lats.append(la)
        r_lons.append(lo)
        r_dates.append(r["created_date"])

    r_lats = np.array(r_lats, dtype=np.float64)
    r_lons = np.array(r_lons, dtype=np.float64)
    r_w = winsorize_by_point(r_lats, r_lons, decay_weights(r_dates),
                             POINT_CAP)
    rodent_grid = bin_points(r_lats, r_lons, r_w)

    lats, lons, dates = [], [], []
    hpd_rows = conn.execute(
        "SELECT bbl, received_date FROM ds_hpd_complaints "
        "WHERE major_category = 'UNSANITARY CONDITION' "
        "AND minor_category = 'PESTS'"
    ).fetchall()
    for r in hpd_rows:
        try:
            bbl = str(int(float(r["bbl"])))
        except (TypeError, ValueError):
            bbl = str(r["bbl"])
        coords = bbl_coords.get(bbl)
        if coords is None:
            continue
        lats.append(coords[0])
        lons.append(coords[1])
        dates.append(r["received_date"])

    hpd_grid = bin_points(np.array(lats, dtype=np.float64),
                          np.array(lons, dtype=np.float64),
                          decay_weights(dates))
    return rodent_grid + hpd_grid


# ── Non-incident layers ──────────────────────────────────────────

def compute_transit(conn, grid_m: np.ndarray) -> np.ndarray:
    """Continuous transit metric — subway-entrance kernel density + bus.

    Subway term: S = Σ over entrances of exp(-d_i / 400) (exact street
    entrances, entry_allowed='YES'), squashed to 100·(1 − exp(−0.7·S)).
    The nearest entrance dominates S (its exp(-d/400) is the largest
    term), so this contains the classic nearest-entrance metric — but S
    also credits the NUMBER of reachable entrances, which distinguishes
    a multi-line walk shed (East Village: L/F/6 stations in range) from
    a cell that merely sits near one outer-branch station.  Pure
    nearest-entrance ranked such network-rich areas ~p65, below dozens
    of one-line areas — contradicting the listing TransitScorer's route
    -diversity story.  Continuous everywhere → no tie blocks.

    Bus term (identical to TransitScorer): min(10, 2 × distinct bus
    routes with a stop within 300 m).
    """
    ent_rows = conn.execute(
        "SELECT entrance_latitude, entrance_longitude FROM ds_subway_entrances "
        "WHERE entry_allowed = 'YES' "
        "AND entrance_latitude IS NOT NULL AND entrance_longitude IS NOT NULL"
    ).fetchall()
    e_lats, e_lons = _safe_floats(ent_rows, "entrance_latitude",
                                  "entrance_longitude")

    raw = np.zeros(ROWS * COLS, dtype=np.float64)
    if len(e_lats) > 0:
        ent_m = np.column_stack((e_lats * LAT_M, e_lons * LNG_M))
        tree = cKDTree(ent_m)
        # Contributions beyond 2.5 km are < exp(-6.25) ≈ 0.2% — skip.
        neighbours = tree.query_ball_point(grid_m, r=2500.0)
        S = np.zeros(ROWS * COLS, dtype=np.float64)
        for i, nbrs in enumerate(neighbours):
            if nbrs:
                d = np.linalg.norm(ent_m[nbrs] - grid_m[i], axis=1)
                S[i] = np.exp(-d / 400.0).sum()
        raw = 100.0 * (1.0 - np.exp(-0.7 * S))

    bus_rows = conn.execute(
        "SELECT latitude, longitude, routes FROM ds_bus_stops "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()
    b_lats, b_lons, b_routes = [], [], []
    for r in bus_rows:
        try:
            la = float(r["latitude"])
            lo = float(r["longitude"])
        except (TypeError, ValueError):
            continue
        if la == 0 or lo == 0:
            continue
        b_lats.append(la)
        b_lons.append(lo)
        b_routes.append(set((r["routes"] or "").split(",")) - {""})

    if b_lats:
        bus_m = np.column_stack((np.array(b_lats) * LAT_M,
                                 np.array(b_lons) * LNG_M))
        bus_tree = cKDTree(bus_m)
        neighbours = bus_tree.query_ball_point(grid_m, r=300.0)
        bus_bonus = np.empty(ROWS * COLS, dtype=np.float64)
        for i, nbrs in enumerate(neighbours):
            routes: set = set()
            for j in nbrs:
                routes |= b_routes[j]
            bus_bonus[i] = min(10.0, 2.0 * len(routes))
        raw = raw + bus_bonus

    return raw.reshape(ROWS, COLS)


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
    """Greenery absolute score via Gaussian convolution.

    Combines street-tree count/canopy (r=200 m) and community gardens
    (r=500 m) using the same sqrt diminishing-returns formula as
    GreeneryScorer._absolute_score.  Kernels use norm="area" so counts
    stay on the count-within-radius scale the caps were tuned for.
    """
    TREE_CAP = 200
    CANOPY_CAP = 2000
    GARDEN_CAP = 3

    k_tree = gaussian_kernel(200, norm="area")
    k_garden = gaussian_kernel(500, norm="area")

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

    tree_count = conv(bin_points(t_lats, t_lons), k_tree)
    canopy_sum = conv(bin_points(t_lats, t_lons, t_dbh), k_tree)

    # ── Community gardens ────────────────────────────────────────
    g_rows = conn.execute(
        "SELECT latitude, longitude FROM ds_community_gardens "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()

    g_lats, g_lons = _safe_floats(g_rows, "latitude", "longitude")

    garden_count = conv(bin_points(g_lats, g_lons), k_garden)

    # ── Absolute score formula ───────────────────────────────────
    tree_pts   = np.sqrt(np.minimum(1.0, tree_count  / TREE_CAP))  * 40
    canopy_pts = np.sqrt(np.minimum(1.0, canopy_sum  / CANOPY_CAP)) * 40
    garden_pts = np.sqrt(np.minimum(1.0, garden_count / GARDEN_CAP)) * 20

    return np.minimum(tree_pts + canopy_pts + garden_pts, 100.0)


def compute_convenience(conn) -> np.ndarray:
    """Amenity density via Gaussian convolution (r=500 m)."""
    rows = conn.execute(
        "SELECT lat, lon FROM ds_amenities "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()

    lats, lons = _safe_floats(rows, "lat", "lon")
    return conv(bin_points(lats, lons), gaussian_kernel(500, norm="area"))


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


# ── Validation ───────────────────────────────────────────────────

def _cell_value(grid: np.ndarray, lat: float, lng: float) -> float:
    """Score at (lat, lng) using the row-0-north grid orientation."""
    ri = int(round((MAX_LAT - lat) / LAT_STEP))
    ci = int(round((lng - MIN_LNG) / LNG_STEP))
    if not (0 <= ri < ROWS and 0 <= ci < COLS):
        return float("nan")
    return float(grid[ri, ci])


REFERENCE_CHECKS = [
    # (label, layer, lat, lng, op, threshold)
    ("Midtown noise",        "noise",   40.7549, -73.9840, "<", 35.0),
    # NB: the originally-proposed Bayside noise point (40.7612,-73.7716)
    # is 42nd Ave & Bell Blvd — the middle of Bayside's bar strip, with
    # 206 noise complaints within 300 m.  The listing pipeline itself
    # (NoiseScorer + frozen citywide baseline) scores that point 25.6,
    # so "> 60" there would contradict the statistical story this map
    # must match.  The assertion uses a residential Bayside block a few
    # streets east (39th Ave & 221st St) instead; the strip point is
    # reported as an informational line.
    ("Bayside noise (resid)", "noise",  40.7625, -73.7645, ">", 60.0),
    ("Times Sq crime",       "crime",   40.7580, -73.9855, "<", 25.0),
    ("Forest Hills crime",   "crime",   40.7146, -73.8437, ">", 60.0),
    ("East Village transit", "transit", 40.7265, -73.9835, ">", 80.0),
    ("Bayside transit",      "transit", 40.7612, -73.7716, "<", 40.0),
]

# Reported but not asserted — context for the reference table.
INFO_CHECKS = [
    ("Bayside noise (Bell Blvd bar strip)", "noise", 40.7612, -73.7716),
]


def validate(scored: dict) -> bool:
    """Distribution spread + reference-point assertions.  True = all pass."""
    print("\n" + "=" * 68)
    print("SELF-VALIDATION")
    print("=" * 68)

    header = (f"{'layer':<12} {'n_res':>7} {'p5':>6} {'p25':>6} {'p50':>6} "
              f"{'p75':>6} {'p95':>6} {'extremes%':>10}")
    print(header)
    print("-" * len(header))

    ok = True
    for name, grid in scored.items():
        vals = grid[~np.isnan(grid)]
        n = len(vals)
        if n == 0:
            print(f"{name:<12} {'0':>7}  — EMPTY LAYER")
            ok = False
            continue
        p = np.percentile(vals, [5, 25, 50, 75, 95])
        extremes = float(np.mean((vals <= 10) | (vals >= 90))) * 100.0
        flag = ""
        if extremes >= 30.0:
            flag = "  << FAIL (>=30%)"
            ok = False
        print(f"{name:<12} {n:>7} {p[0]:>6.1f} {p[1]:>6.1f} {p[2]:>6.1f} "
              f"{p[3]:>6.1f} {p[4]:>6.1f} {extremes:>9.1f}%{flag}")

    print("\nReference-point assertions:")
    for label, layer, lat, lng, op, thr in REFERENCE_CHECKS:
        v = _cell_value(scored[layer], lat, lng)
        if math.isnan(v):
            passed = False
        elif op == "<":
            passed = v < thr
        else:
            passed = v > thr
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {label:<22} score={v:6.1f}  expected {op} {thr}")
        ok = ok and passed

    for label, layer, lat, lng in INFO_CHECKS:
        v = _cell_value(scored[layer], lat, lng)
        print(f"  [info] {label}: score={v:.1f} "
              f"(listing pipeline scores this point 25.6)")

    return ok


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("Heatmap grid generator (residential rates — numpy/scipy)")
    print("=" * 60)

    conn = get_connection()
    store = DataStore(conn)

    # Ensure all datasets are downloaded (no-op if already cached)
    for ds in ("crime", "noise", "street_trees", "community_gardens",
               "parks", "amenities", "pluto", "hpd_complaints",
               "subway_entrances", "bus_stops"):
        store.ensure_downloaded(ds, quiet=True)

    print(f"Grid: {ROWS} rows × {COLS} cols = {ROWS * COLS:,} cells")
    print(f"Resolution: ~{CELL_H:.0f} m × ~{CELL_W:.0f} m")
    print()

    grid_m = grid_coords_m()
    t_total = time.time()

    # ── Step 1: Residential-units denominators ───────────────────
    t0 = time.time()
    units_grid, bbl_coords = load_pluto(conn)
    k400 = gaussian_kernel(400, norm="mean")
    k300 = gaussian_kernel(300, norm="mean")
    k100 = gaussian_kernel(100, norm="mean")
    units400 = conv(units_grid, k400)
    units300 = conv(units_grid, k300)
    units100 = conv(units_grid, k100)
    res400 = units400 >= RES_UNITS_MIN
    res300 = units300 >= RES_UNITS_MIN
    res100 = units100 >= RES_UNITS_MIN
    print(f"[pluto]       {time.time() - t0:.1f}s  "
          f"({units_grid.sum():,.0f} residential units; "
          f"{res400.sum():,} residential cells @400m)")

    # ── Step 2: Incident rates (decay-weighted / units) ──────────
    t0 = time.time()
    crime_rate = conv(crime_points(conn), k400) / np.maximum(units400,
                                                             RES_UNITS_MIN)
    print(f"[crime]       {time.time() - t0:.1f}s")

    t0 = time.time()
    noise_rate = conv(noise_points(conn), k300) / np.maximum(units300,
                                                             RES_UNITS_MIN)
    print(f"[noise]       {time.time() - t0:.1f}s")

    t0 = time.time()
    pest_rate = conv(pest_points(conn, bbl_coords), k100) / np.maximum(
        units100, RES_UNITS_MIN)
    print(f"[pest]        {time.time() - t0:.1f}s")

    # ── Step 3: Non-incident raw layers ──────────────────────────
    t0 = time.time()
    transit_raw = compute_transit(conn, grid_m)
    print(f"[transit]     {time.time() - t0:.1f}s  "
          f"(max raw: {transit_raw.max():.1f})")

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
          f"(max amenity density: {convenience_raw.max():.0f})")

    # ── Step 4: Residential percentile ranking ───────────────────
    # Every layer is ranked ONLY across residential cells → uniform
    # residential distribution (full gradient where people live).
    # Non-residential cells (water / parks / industrial / out-of-city)
    # are null → uncolored.  Incident layers are inverted so that
    # higher score = safer/quieter = green.
    print("\nRanking across residential cells...")
    scored = {
        "crime":       percentile_rank(crime_rate, res400, reverse=True),
        "noise":       percentile_rank(noise_rate, res300, reverse=True),
        "pest":        percentile_rank(pest_rate,  res100, reverse=True),
        "transit":     percentile_rank(transit_raw, res400),
        "convenience": percentile_rank(convenience_raw, res400),
    }

    # Green space: max(parks, greenery) + secondary bonus, UNCAPPED for
    # ranking.  The old min(100, …) clip collapsed 56% of residential
    # cells into one tied value (score plateau at ~72); ranking the
    # uncapped blend keeps the same ordering for unclipped cells while
    # restoring a full gradient at the green end.
    green_blend = np.maximum(parks_raw, greenery_raw)
    secondary = np.minimum(parks_raw, greenery_raw)
    green_space = green_blend + secondary * 0.15
    scored["green_space"] = percentile_rank(green_space, res400)

    for name in ("crime", "noise", "transit", "green_space",
                 "convenience", "pest"):
        save_grid(name, scored[name])

    ok = validate(scored)

    conn.close()
    elapsed = time.time() - t_total
    print(f"\nDone! 6 heatmap grids written to {OUT_DIR}")
    print(f"Total time: {elapsed:.1f}s")
    if not ok:
        print("VALIDATION FAILED — see above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
