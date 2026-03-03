#!/usr/bin/env python3
"""
Generate heatmap grid data for every location-based scoring dimension.

Creates a regular lat/lng grid across NYC, runs the scoring modules on
each grid cell, extracts **raw component values** (not pre-normalised
scores), applies a water mask, then percentile-ranks the land cells
to maximize visual variance.

Key design decisions:
  - Crime & noise use raw density (weighted_total / complaint_count)
    percentile-ranked, NOT the scorer's median-inverse output which
    collapses to binary 0/100 across a sparse grid.
  - Green space uses max(parks, greenery) so cells inside large parks
    (Central Park) aren't diluted by low street-tree counts.
  - Pests uses raw combined count (HPD + 311 rodent) density.
  - All outputs are percentile-ranked on land cells only.

Usage:
    python scripts/generate_heatmap.py

Output files:
    frontend/public/heatmap/crime.json
    frontend/public/heatmap/noise.json
    frontend/public/heatmap/transit.json
    frontend/public/heatmap/green_space.json
    frontend/public/heatmap/convenience.json
    frontend/public/heatmap/pest.json
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
from apthunt.scoring.pest import PestScorer

# ── Grid parameters ──────────────────────────────────────────────

# Bounding box covering all 5 boroughs
MIN_LAT, MAX_LAT = 40.49, 40.92
MIN_LNG, MAX_LNG = -74.27, -73.68

# ~400m resolution (good balance of detail vs compute time)
LAT_STEP = 0.0036   # ~400m north-south
LNG_STEP = 0.0048   # ~400m east-west at NYC latitude

OUT_DIR = os.path.join(ROOT, "frontend", "public", "heatmap")


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


CellKey = tuple[int, int]


def run_scorer_full(scorer, conn, grid_points):
    """Run a scorer and return BOTH scores and raw components per cell."""
    results = scorer.score(conn, grid_points)
    scores: dict[CellKey, float] = {}
    components: dict[CellKey, dict] = {}
    for r in results:
        _, row_s, col_s = r.listing_id.split("_")
        key = (int(row_s), int(col_s))
        scores[key] = r.score
        components[key] = r.components
    return scores, components


def percentile_rank(
    raw_map: dict[CellKey, float],
    land_cells: set[CellKey],
    *,
    reverse: bool = False,
) -> dict[CellKey, float]:
    """Percentile-rank raw values across land cells only.

    Args:
        raw_map:    {cell: raw_value}
        land_cells: set of land cells to include
        reverse:    if True, LOWER raw values get HIGHER percentiles
                    (use for "fewer = better" metrics like crime, noise, pests)
    """
    land_scores = {k: v for k, v in raw_map.items() if k in land_cells}
    if not land_scores:
        return {}

    items = sorted(land_scores.items(), key=lambda x: x[1])
    n = len(items)
    if n == 1:
        return {items[0][0]: 50.0}

    ranked: dict[CellKey, float] = {}
    i = 0
    while i < n:
        j = i
        while j < n and items[j][1] == items[i][1]:
            j += 1
        avg_rank = (i + j - 1) / 2.0
        pct = round(avg_rank / (n - 1) * 100, 1)
        if reverse:
            pct = round(100.0 - pct, 1)
        for k in range(i, j):
            ranked[items[k][0]] = pct
        i = j
    return ranked


def build_water_mask(
    all_components: dict[str, dict[CellKey, dict]],
    all_scores: dict[str, dict[CellKey, float]],
) -> set[CellKey]:
    """Identify water/void cells.

    A cell is water if ALL of:
        - greenery score == 0 (no trees, canopy, or gardens)
        - convenience score == 0 (no amenities)
    NYC land always has street trees or some amenity within 500m.
    """
    greenery_s = all_scores.get("greenery", {})
    convenience_s = all_scores.get("convenience", {})

    all_keys = set(greenery_s.keys()) | set(convenience_s.keys())
    water: set[CellKey] = set()

    for key in all_keys:
        g = greenery_s.get(key, 0)
        c = convenience_s.get(key, 0)
        if g == 0 and c == 0:
            water.add(key)

    return water


def save_grid(
    name: str,
    score_map: dict[CellKey, float],
    rows: int,
    cols: int,
) -> None:
    """Write a grid as compact JSON. Missing cells become null."""
    scores: list[list[float | None]] = []
    for r in range(rows):
        row_data: list[float | None] = []
        for c in range(cols):
            raw = score_map.get((r, c))
            row_data.append(raw if raw is not None else None)
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


def print_dist(ranked: dict[CellKey, float]) -> None:
    """Print quick percentile distribution."""
    vals = sorted(ranked.values())
    n = len(vals)
    if not n:
        return
    print(f"  p5={vals[int(n*0.05)]:.0f}  p25={vals[int(n*0.25)]:.0f}  "
          f"p50={vals[n//2]:.0f}  p75={vals[int(n*0.75)]:.0f}  "
          f"p95={vals[int(n*0.95)]:.0f}")


def main() -> None:
    print("=" * 60)
    print("Heatmap grid generator (density-based)")
    print("=" * 60)

    conn = get_connection()
    store = DataStore(conn)
    cache = BlockCache(conn, ttl_days=30)
    transit = TransitData(os.path.join(ROOT, "data", "stops.txt"))

    grid_points, rows, cols = make_grid()
    total = len(grid_points)
    print(f"Grid: {rows} rows × {cols} cols = {total:,} cells")
    print(f"Resolution: ~{LAT_STEP * 111_320:.0f}m × ~{LNG_STEP * 85_000:.0f}m")
    print()

    # ── Step 1: Run ALL scorers, extracting raw components ───────
    scorer_configs = {
        "crime":       CrimeScorer(store, cache),
        "noise":       NoiseScorer(store, cache),
        "transit":     TransitScorer(transit, cache),
        "parks":       ParksScorer(store, cache),
        "greenery":    GreeneryScorer(store, cache),
        "convenience": ConvenienceScorer(store, cache),
        "pest":        PestScorer(store, cache),
    }

    all_scores: dict[str, dict[CellKey, float]] = {}
    all_components: dict[str, dict[CellKey, dict]] = {}

    for name, scorer in scorer_configs.items():
        print(f"[{name}] Scoring {total:,} grid cells...")
        t0 = time.time()
        scores, comps = run_scorer_full(scorer, conn, grid_points)
        elapsed = time.time() - t0
        print(f"  {len(scores):,} scores in {elapsed:.1f}s")
        all_scores[name] = scores
        all_components[name] = comps

    # ── Step 2: Water mask ───────────────────────────────────────
    water_mask = build_water_mask(all_components, all_scores)
    land_cells = set()
    for sm in all_scores.values():
        land_cells.update(sm.keys())
    land_cells -= water_mask

    print(f"\nWater mask: {len(water_mask):,} water cells, "
          f"{len(land_cells):,} land cells")

    # ── Step 3: Build raw density maps from components ───────────
    #
    # For density-based metrics we extract the RAW COUNT values
    # from scorer components (not the pre-normalised 0-100 score)
    # and percentile-rank them ourselves. This avoids the bimodal
    # collapse caused by median_inverse_scores on a sparse grid.

    # --- CRIME: density = crime_weighted_total ---
    # Higher weighted_total = more crime = worse
    print("\n[crime] Building density from crime_weighted_total...")
    crime_density: dict[CellKey, float] = {}
    for key, comp in all_components["crime"].items():
        crime_density[key] = comp.get("crime_weighted_total", 0.0)
    crime_ranked = percentile_rank(crime_density, land_cells, reverse=True)
    save_grid("crime", crime_ranked, rows, cols)
    print_dist(crime_ranked)

    # --- NOISE: density = noise_complaint_count ---
    # Higher count = noisier = worse
    print("\n[noise] Building density from noise_complaint_count...")
    noise_density: dict[CellKey, float] = {}
    for key, comp in all_components["noise"].items():
        noise_density[key] = comp.get("noise_complaint_count", 0)
    noise_ranked = percentile_rank(noise_density, land_cells, reverse=True)
    save_grid("noise", noise_ranked, rows, cols)
    print_dist(noise_ranked)

    # --- TRANSIT: use scorer's score directly (absolute formula, not bimodal) ---
    print("\n[transit] Percentile-ranking transit scores...")
    transit_ranked = percentile_rank(all_scores["transit"], land_cells)
    save_grid("transit", transit_ranked, rows, cols)
    print_dist(transit_ranked)

    # --- GREEN SPACE: intelligent blend of parks + greenery ---
    # Parks score = proximity × quality of best nearby park (0-100)
    # Greenery score = street trees + canopy + community gardens (0-100)
    #
    # Problem with simple average: Central Park gets high parks score
    # but low greenery (street tree census doesn't count park trees).
    # Fix: use weighted-max approach:
    #   - If parks ≥ 60, it dominates (you're near a real park)
    #   - Otherwise blend with greenery to capture tree-lined streets
    print("\n[green_space] Building intelligent parks+greenery blend...")
    green_space: dict[CellKey, float] = {}
    for key in land_cells:
        p = all_scores.get("parks", {}).get(key, 0)
        g = all_scores.get("greenery", {}).get(key, 0)
        # Weighted blend: parks dominates when high
        if p >= 60:
            # Near a significant park — let parks score dominate
            # but still credit greenery slightly
            green_space[key] = p * 0.75 + g * 0.25
        else:
            # Not near a major park — greenery matters more
            # Use the higher of the two, blended
            green_space[key] = max(p, g) * 0.6 + min(p, g) * 0.4
    gs_ranked = percentile_rank(green_space, land_cells)
    save_grid("green_space", gs_ranked, rows, cols)
    print_dist(gs_ranked)

    # --- CONVENIENCE: use component counts for richer density ---
    # Sum all amenity counts (grocery + pharmacy + gym + laundry + dining)
    # This gives raw density, not the sqrt-diminishing-returns score
    print("\n[convenience] Building density from amenity counts...")
    conv_density: dict[CellKey, float] = {}
    for key, comp in all_components["convenience"].items():
        total_amenities = (
            comp.get("convenience_grocery", 0) +
            comp.get("convenience_pharmacy", 0) +
            comp.get("convenience_gym", 0) +
            comp.get("convenience_laundry", 0) +
            comp.get("convenience_dining", 0)
        )
        conv_density[key] = total_amenities
    conv_ranked = percentile_rank(conv_density, land_cells)
    save_grid("convenience", conv_ranked, rows, cols)
    print_dist(conv_ranked)

    # --- PESTS: density = pest_total (HPD complaints + 311 rodent) ---
    # Higher = more pests = worse
    print("\n[pest] Building density from pest_total...")
    pest_density: dict[CellKey, float] = {}
    for key, comp in all_components["pest"].items():
        pest_density[key] = comp.get("pest_total", 0)
    pest_ranked = percentile_rank(pest_density, land_cells, reverse=True)
    save_grid("pest", pest_ranked, rows, cols)
    print_dist(pest_ranked)

    conn.close()
    print(f"\nDone! 6 heatmap grids written to {OUT_DIR}")


if __name__ == "__main__":
    main()
