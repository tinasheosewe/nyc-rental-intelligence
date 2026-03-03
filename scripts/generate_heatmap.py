#!/usr/bin/env python3
"""
Generate heatmap grid data for every location-based scoring dimension.

Creates a regular lat/lng grid across NYC, runs the scoring modules on
each grid cell (as "virtual listings"), applies the grade curve, and
writes compact JSON files to frontend/public/heatmap/.

Usage:
    python scripts/generate_heatmap.py

Output files:
    frontend/public/heatmap/crime.json
    frontend/public/heatmap/noise.json
    frontend/public/heatmap/transit.json
    frontend/public/heatmap/green_space.json  (parks + greenery averaged)
    frontend/public/heatmap/convenience.json
"""

from __future__ import annotations

import json
import os
import sys
import time

# ── Path setup ───────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt.db import get_connection
from apthunt.data.data_store import DataStore
from apthunt.data.block_cache import BlockCache
from apthunt.data.transit_data import TransitData
from apthunt import geohash as gh_mod
from apthunt.scoring.crime import CrimeScorer
from apthunt.scoring.noise import NoiseScorer
from apthunt.scoring.transit import TransitScorer
from apthunt.scoring.parks import ParksScorer
from apthunt.scoring.greenery import GreeneryScorer
from apthunt.scoring.convenience import ConvenienceScorer

# ── Grid parameters ──────────────────────────────────────────────

# Bounding box covering all 5 boroughs
MIN_LAT, MAX_LAT = 40.49, 40.92
MIN_LNG, MAX_LNG = -74.27, -73.68

# ~400m resolution (good balance of detail vs compute time)
LAT_STEP = 0.0036   # ~400m north-south
LNG_STEP = 0.0048   # ~400m east-west at NYC latitude

OUT_DIR = os.path.join(ROOT, "frontend", "public", "heatmap")

# Grade curve exponent (must match api/routers/listings.py)
GRADE_EXPONENT = 0.4


def grade_curve(raw: float) -> float:
    """Apply the same grade curve used by the API."""
    if raw <= 0:
        return 0.0
    return round(100.0 * (raw / 100.0) ** GRADE_EXPONENT, 1)


def make_grid() -> tuple[list[dict], int, int]:
    """Generate a grid of virtual 'listings' spanning NYC."""
    points: list[dict] = []
    lat = MAX_LAT
    row = 0
    while lat >= MIN_LAT:
        lng = MIN_LNG
        col = 0
        while lng <= MAX_LNG:
            points.append({
                "id": f"g_{row}_{col}",
                "lat": lat,
                "lon": lng,
                "geohash": gh_mod.encode(lat, lng),
            })
            lng += LNG_STEP
            col += 1
        lat -= LAT_STEP
        row += 1
    cols = len([1 for p in points if p["id"].startswith("g_0_")])
    return points, row, cols


def run_scorer(scorer, conn, grid_points) -> dict[tuple[int, int], float]:
    """Run a scorer on the grid and return {(row, col): score} mapping."""
    results = scorer.score(conn, grid_points)
    score_map: dict[tuple[int, int], float] = {}
    for r in results:
        _, row_s, col_s = r.listing_id.split("_")
        score_map[(int(row_s), int(col_s))] = r.score
    return score_map


def save_grid(name: str, score_map: dict[tuple[int, int], float],
              rows: int, cols: int) -> None:
    """Write a grid as compact JSON with grade-curved scores."""
    scores: list[list[float | None]] = []
    for r in range(rows):
        row_data: list[float | None] = []
        for c in range(cols):
            raw = score_map.get((r, c))
            row_data.append(grade_curve(raw) if raw is not None else None)
        scores.append(row_data)

    data = {
        "bounds": {
            "minLat": MIN_LAT,
            "maxLat": MAX_LAT,
            "minLng": MIN_LNG,
            "maxLng": MAX_LNG,
        },
        "rows": rows,
        "cols": cols,
        "latStep": LAT_STEP,
        "lngStep": LNG_STEP,
        "scores": scores,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    size_kb = os.path.getsize(path) / 1024
    print(f"  → {path} ({size_kb:.1f} KB)")


def main() -> None:
    print("=" * 60)
    print("Heatmap grid generator")
    print("=" * 60)

    conn = get_connection()
    store = DataStore(conn)
    cache = BlockCache(conn, ttl_days=30)  # Longer TTL for grid data
    transit = TransitData(os.path.join(ROOT, "data", "stops.txt"))

    grid_points, rows, cols = make_grid()
    total = len(grid_points)
    print(f"Grid: {rows} rows × {cols} cols = {total:,} cells")
    print(f"Resolution: ~{LAT_STEP * 111_320:.0f}m × ~{LNG_STEP * 85_000:.0f}m")
    print()

    # Score each dimension
    scorers: dict[str, object] = {
        "crime": CrimeScorer(store, cache),
        "noise": NoiseScorer(store, cache),
        "transit": TransitScorer(transit, cache),
        "parks": ParksScorer(store, cache),
        "greenery": GreeneryScorer(store, cache),
        "convenience": ConvenienceScorer(store, cache),
    }

    all_maps: dict[str, dict[tuple[int, int], float]] = {}

    for name, scorer in scorers.items():
        print(f"[{name}] Scoring {total:,} grid cells...")
        t0 = time.time()
        score_map = run_scorer(scorer, conn, grid_points)
        elapsed = time.time() - t0
        print(f"  {len(score_map):,} scores in {elapsed:.1f}s")
        all_maps[name] = score_map

        # Save individual dimension (except parks/greenery — blended below)
        if name not in ("parks", "greenery"):
            save_grid(name, score_map, rows, cols)

    # Blend parks + greenery → green_space
    print("\n[green_space] Blending parks + greenery...")
    parks_map = all_maps["parks"]
    greenery_map = all_maps["greenery"]
    blended: dict[tuple[int, int], float] = {}
    all_keys = set(parks_map.keys()) | set(greenery_map.keys())
    for key in all_keys:
        p = parks_map.get(key)
        g = greenery_map.get(key)
        if p is not None and g is not None:
            blended[key] = (p + g) / 2
        elif p is not None:
            blended[key] = p
        elif g is not None:
            blended[key] = g
    save_grid("green_space", blended, rows, cols)

    conn.close()
    print(f"\nDone! {len(all_maps)} dimensions processed.")


if __name__ == "__main__":
    main()
