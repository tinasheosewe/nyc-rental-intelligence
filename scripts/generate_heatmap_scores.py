#!/usr/bin/env python3
"""
Scorer-driven heatmap generation — one code path, zero divergence.

The old generator (scripts/generate_heatmap.py) maintained PARALLEL
formulas for each overlay, which silently diverged from the scorers as
they evolved (measured: parks beside McCarren scored 97.9 by the real
scorer while the green_space layer showed 7.3 two blocks from
Transmitter Park). This script kills the dual pipeline: every overlay
cell is scored by THE SCORERS THEMSELVES over the citywide grid —
affordable now that the in-memory fast path answers spatial queries in
microseconds.

Output format/paths are byte-compatible with the old generator
(frontend/public/heatmap/<layer>.json, row 0 = north).

Layers:
    crime, noise, pest, transit, convenience  → that scorer's score
    green_space                               → mean(parks, greenery)

Cells with no residential context score None (uncolored), matching the
scorers' own null semantics via a kernel-units threshold.

Usage:
    python3 scripts/generate_heatmap_scores.py            # all layers
    python3 scripts/generate_heatmap_scores.py --only crime,green_space
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt import geohash
from apthunt.db import get_connection, DB_PATH
from apthunt.data.data_store import DataStore
from apthunt.data.block_cache import BlockCache
from apthunt.scoring.utils import kernel_weighted_units

from apthunt.scoring.crime import CrimeScorer
from apthunt.scoring.noise import NoiseScorer
from apthunt.scoring.pest import PestScorer
from apthunt.scoring.transit import TransitScorer
from apthunt.scoring.convenience import ConvenienceScorer
from apthunt.scoring.parks import ParksScorer
from apthunt.scoring.greenery import GreeneryScorer
from apthunt.data.transit_data import TransitData

# Grid parameters — MUST match the old generator (frontend contract).
MIN_LAT, MAX_LAT = 40.49, 40.92
MIN_LNG, MAX_LNG = -74.27, -73.68
LAT_STEP = 0.0036
LNG_STEP = 0.0048
ROWS = int((MAX_LAT - MIN_LAT) / LAT_STEP) + 1
COLS = int((MAX_LNG - MIN_LNG) / LNG_STEP) + 1

# Residential mask: same spirit as the incident layers' units>=50 rule.
MIN_KERNEL_UNITS = 50.0
UNITS_RADIUS_M = 400.0

OUT_DIR = os.path.join(ROOT, "frontend", "public", "heatmap")
STOPS_PATH = os.path.join(ROOT, "data", "stops.txt")


def build_cells(conn, store):
    """All grid cells with residential context, as synthetic listings."""
    cells = []
    t0 = time.time()
    for r in range(ROWS):
        lat = MAX_LAT - r * LAT_STEP  # row 0 = north
        for c in range(COLS):
            lon = MIN_LNG + c * LNG_STEP
            units = kernel_weighted_units(
                store, lat, lon, UNITS_RADIUS_M, floor=0.0,
            )
            if units < MIN_KERNEL_UNITS:
                continue
            cells.append({
                "id": f"hm:{r}:{c}", "row": r, "col": c,
                "lat": lat, "lon": lon,
                "geohash": geohash.encode(lat, lon),
                "price": None, "beds": None, "neighborhood": None,
                "borough": None, "net_effective_price": None,
            })
    print(f"  residential cells: {len(cells)}/{ROWS*COLS} in {time.time()-t0:.1f}s")
    return cells


def write_layer(name, cells, scores_by_id):
    grid = [[None] * COLS for _ in range(ROWS)]
    for cell in cells:
        v = scores_by_id.get(cell["id"])
        if v is not None:
            grid[cell["row"]][cell["col"]] = round(v, 1)
    out = {
        "bounds": {"minLat": MIN_LAT, "maxLat": MAX_LAT,
                   "minLng": MIN_LNG, "maxLng": MAX_LNG},
        "rows": ROWS, "cols": COLS,
        "latStep": LAT_STEP, "lngStep": LNG_STEP,
        "scores": grid,
    }
    path = os.path.join(OUT_DIR, f"{name}.json")
    with open(path, "w") as f:
        json.dump(out, f)
    n = sum(1 for row in grid for v in row if v is not None)
    print(f"  ✓ {name}: {n} colored cells → {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=str, default=None)
    ap.add_argument("--db", type=str, default=DB_PATH)
    args = ap.parse_args()

    conn = get_connection(args.db)
    store = DataStore(conn)
    cache = BlockCache(conn)
    try:
        from apthunt.data.spatial_index import activate_fast_path
        activate_fast_path(conn, store)
    except Exception as exc:
        print(f"fast path unavailable ({exc}) — this will be slow")

    cells = build_cells(conn, store)

    layers = {
        "crime": lambda: CrimeScorer(store, cache),
        "noise": lambda: NoiseScorer(store, cache),
        "pest": lambda: PestScorer(store, cache),
        "transit": lambda: TransitScorer(TransitData(STOPS_PATH), cache),
        "convenience": lambda: ConvenienceScorer(store, cache),
        "green_space": None,  # blend of parks + greenery below
    }
    wanted = set((args.only or ",".join(layers)).split(","))

    for name, factory in layers.items():
        if name not in wanted or name == "green_space":
            continue
        t0 = time.time()
        results = factory().score(conn, cells)
        write_layer(name, cells,
                    {r.listing_id: r.score for r in results})
        print(f"    ({time.time()-t0:.1f}s)")

    if "green_space" in wanted:
        t0 = time.time()
        parks = {r.listing_id: r.score
                 for r in ParksScorer(store, cache).score(conn, cells)}
        green = {r.listing_id: r.score
                 for r in GreeneryScorer(store, cache).score(conn, cells)}
        blend = {}
        for cid in parks.keys() | green.keys():
            vals = [v for v in (parks.get(cid), green.get(cid)) if v is not None]
            blend[cid] = sum(vals) / len(vals) if vals else None
        write_layer("green_space", cells, blend)
        print(f"    ({time.time()-t0:.1f}s)")

    cache.flush()
    conn.close()
    print("Done — overlays now share the scorers' code path exactly.")


if __name__ == "__main__":
    main()
