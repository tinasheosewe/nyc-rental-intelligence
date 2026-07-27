#!/usr/bin/env python3
"""
Scorer-driven heatmap generation — one code path, zero divergence.

Every overlay cell is scored by THE SCORERS THEMSELVES (the old parallel
formulas drifted from the product and were deleted), sampled where
people actually live:

Cell sampling (v2): each ~430x400 m cell is scored at up to
ANCHORS_PER_CELL residential PLUTO lots (largest unitsres first) inside
the cell footprint, and the cell value is the units-weighted mean of the
anchor scores.  The v1 approach — one sample at the cell's NW corner —
misrepresented any cell whose corner landed somewhere unrepresentative:
the cell containing Cooper Park sampled the NYCHA superblock and showed
green_space 29.9 while rowhouses inside the same cell scored 86.1.
Cells without >= MIN_CELL_UNITS residential units stay uncolored (parks,
industry, water render as basemap, not as scores).

Layers:
    crime, noise, pest, transit, convenience  → that scorer's score
    green_space → 0.65*max(parks, greenery) + 0.35*min(parks, greenery)
        per anchor.  A plain mean let a street-tree score of 2 halve an
        85-point parks score, painting "no green space" at a park's
        doorstep; max-weighted keeps the stronger green signal dominant
        while still crediting places that have both.

Output format/paths are unchanged (frontend/public/heatmap/<layer>.json,
row 0 = north).

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
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt import geohash
from apthunt.db import get_connection, DB_PATH
from apthunt.data.data_store import DataStore
from apthunt.data.block_cache import BlockCache

from apthunt.scoring.crime import CrimeScorer
from apthunt.scoring.noise import NoiseScorer
from apthunt.scoring.pest import PestScorer
from apthunt.scoring.transit import TransitScorer
from apthunt.scoring.convenience import ConvenienceScorer
from apthunt.scoring.parks import ParksScorer
from apthunt.scoring.greenery import GreeneryScorer
from apthunt.data.transit_data import TransitData

# Grid parameters — frontend contract, do not change without the client.
MIN_LAT, MAX_LAT = 40.49, 40.92
MIN_LNG, MAX_LNG = -74.27, -73.68
LAT_STEP = 0.0036
LNG_STEP = 0.0048
ROWS = int((MAX_LAT - MIN_LAT) / LAT_STEP) + 1
COLS = int((MAX_LNG - MIN_LNG) / LNG_STEP) + 1

ANCHORS_PER_CELL = 3
MIN_CELL_UNITS = 10.0     # total residential units required to color a cell

OUT_DIR = os.path.join(ROOT, "frontend", "public", "heatmap")
STOPS_PATH = os.path.join(ROOT, "data", "stops.txt")


def build_anchors(conn):
    """Residential anchor listings per grid cell.

    Returns (anchor_listings, anchor_weight, cell_of_anchor):
      anchor_listings: synthetic listing dicts for the scorers
      anchor_weight:   {anchor_id: unitsres weight}
      cell_of_anchor:  {anchor_id: (row, col)}
    """
    t0 = time.time()
    by_cell = defaultdict(list)
    for lat, lon, units in conn.execute(
        "SELECT latitude, longitude, CAST(unitsres AS REAL) FROM ds_pluto "
        "WHERE CAST(unitsres AS REAL) >= 1 "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL"
    ):
        r = int((MAX_LAT - lat) / LAT_STEP)
        c = int((lon - MIN_LNG) / LNG_STEP)
        if 0 <= r < ROWS and 0 <= c < COLS:
            by_cell[(r, c)].append((units, lat, lon))

    listings, weight, cell_of = [], {}, {}
    n_cells = 0
    for (r, c), lots in by_cell.items():
        total = sum(u for u, _, _ in lots)
        if total < MIN_CELL_UNITS:
            continue
        n_cells += 1
        lots.sort(reverse=True)
        for k, (units, lat, lon) in enumerate(lots[:ANCHORS_PER_CELL]):
            aid = f"hm:{r}:{c}:{k}"
            listings.append({
                "id": aid, "lat": lat, "lon": lon,
                # precision 8 (~38m): anchors are distinct lots — the
                # scorers' block-dedupe must not collapse them.
                "geohash": geohash.encode(lat, lon, precision=8),
                "price": None, "beds": None, "neighborhood": None,
                "borough": None, "net_effective_price": None,
            })
            weight[aid] = max(units, 1.0)
            cell_of[aid] = (r, c)
    print(f"  {n_cells} residential cells, {len(listings)} anchors "
          f"in {time.time()-t0:.1f}s")
    return listings, weight, cell_of


def aggregate(scores_by_anchor, weight, cell_of):
    """Units-weighted mean of anchor scores per cell."""
    num, den = defaultdict(float), defaultdict(float)
    for aid, s in scores_by_anchor.items():
        if s is None:
            continue
        rc = cell_of[aid]
        w = weight[aid]
        num[rc] += w * s
        den[rc] += w
    return {rc: num[rc] / den[rc] for rc in num if den[rc] > 0}


def write_layer(name, cell_values):
    grid = [[None] * COLS for _ in range(ROWS)]
    for (r, c), v in cell_values.items():
        grid[r][c] = round(v, 1)
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
    print(f"  ✓ {name}: {len(cell_values)} colored cells → {path}")


def green_blend(p, g):
    """Park-dominant green blend; None-tolerant."""
    vals = [v for v in (p, g) if v is not None]
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    return 0.65 * max(vals) + 0.35 * min(vals)


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

    anchors, weight, cell_of = build_anchors(conn)

    layers = {
        "crime": lambda: CrimeScorer(store, cache),
        "noise": lambda: NoiseScorer(store, cache),
        "pest": lambda: PestScorer(store, cache),
        "transit": lambda: TransitScorer(TransitData(STOPS_PATH), cache),
        "convenience": lambda: ConvenienceScorer(store, cache),
        "green_space": None,  # blended below
    }
    wanted = set((args.only or ",".join(layers)).split(","))

    for name, factory in layers.items():
        if name not in wanted or name == "green_space":
            continue
        t0 = time.time()
        results = factory().score(conn, anchors)
        by_anchor = {r.listing_id: r.score for r in results}
        write_layer(name, aggregate(by_anchor, weight, cell_of))
        print(f"    ({time.time()-t0:.1f}s)")

    if "green_space" in wanted:
        t0 = time.time()
        parks = {r.listing_id: r.score
                 for r in ParksScorer(store, cache).score(conn, anchors)}
        green = {r.listing_id: r.score
                 for r in GreeneryScorer(store, cache).score(conn, anchors)}
        blended = {aid: green_blend(parks.get(aid), green.get(aid))
                   for aid in set(parks) | set(green)}
        write_layer("green_space", aggregate(blended, weight, cell_of))
        print(f"    ({time.time()-t0:.1f}s)")

    cache.flush()
    conn.close()
    print("Done — overlays share the scorers' code path, sampled at "
          "residential anchors.")


if __name__ == "__main__":
    main()
