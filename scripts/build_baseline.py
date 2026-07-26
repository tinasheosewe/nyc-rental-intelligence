#!/usr/bin/env python3
"""
Build the frozen citywide baseline distributions for every geo scorer.

Samples residential NYC (PLUTO lots with unitsres > 0, deduped to
geohash-7 cells), runs each converted scorer over the sample as synthetic
listings, harvests the scorer's declared ``baseline_component`` raw metric,
and stores a 1001-point quantile grid in ``baseline_dist``.

After this runs, scorers stop being batch-relative: a single pasted
listing scores against the city, absolutely and reproducibly.

Usage:
    python3 scripts/build_baseline.py                 # default 8000 cells
    python3 scripts/build_baseline.py --cells 15000
    python3 scripts/build_baseline.py --only crime,pest
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt import geohash
from apthunt.db import get_connection, DB_PATH
from apthunt.data.data_store import DataStore
from apthunt.data.block_cache import BlockCache
from apthunt.scoring import baseline as bl

from apthunt.scoring.crime import CrimeScorer
from apthunt.scoring.noise import NoiseScorer
from apthunt.scoring.pest import PestScorer
from apthunt.scoring.building_violations import BuildingViolationsScorer
from apthunt.scoring.management import ManagementScorer
from apthunt.scoring.parks import ParksScorer
from apthunt.scoring.greenery import GreeneryScorer
from apthunt.scoring.schools import SchoolsScorer
from apthunt.scoring.shelter import ShelterScorer
from apthunt.scoring.convenience import ConvenienceScorer
from apthunt.scoring.bedbug import BedbugScorer
from apthunt.scoring.street_danger import StreetDangerScorer
from apthunt.scoring.air_quality import AirQualityScorer
from apthunt.scoring.road_exposure import RoadExposureScorer
from apthunt.scoring.deal import DealScorer
from apthunt.scoring.unit_amenities import UnitAmenitiesScorer

RANDOM_SEED = 20260726  # reproducible sample


def residential_cells(conn, n_cells: int) -> list:
    """Sample geohash-7 cells that contain residential units (PLUTO)."""
    rows = conn.execute(
        "SELECT latitude, longitude FROM ds_pluto "
        "WHERE CAST(unitsres AS REAL) > 0 "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()
    cells = {}
    for lat, lon in rows:
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        gh = geohash.encode(lat, lon)
        if gh not in cells:
            cells[gh] = (lat, lon)
    all_cells = sorted(cells.items())  # deterministic order before sampling
    rng = random.Random(RANDOM_SEED)
    if len(all_cells) > n_cells:
        all_cells = rng.sample(all_cells, n_cells)
    return all_cells


def synthetic_listings(cells: list) -> list:
    return [
        {
            "id": f"baseline:{gh}",
            "lat": lat,
            "lon": lon,
            "geohash": gh,
            "price": None,
            "beds": None,
            "neighborhood": None,
            "borough": None,
            "net_effective_price": None,
        }
        for gh, (lat, lon) in cells
    ]


def build_from_listings(conn, scorers) -> None:
    """Baseline building-level dimensions against the ACTIVE LISTING
    population instead of residential grid cells.

    Rationale (measured): residential cells are dominated by 1-4 unit
    owner-occupied homes that never accumulate violations/complaints —
    ranking a rental building against them puts every normal apartment
    building in a bad percentile (avg building_violations score was 22-34
    for real rental stock vs 53 for rowhouses). The renter's honest
    reference class is "buildings on the rental market".

    Reads each scorer's baseline_component column straight off the
    listings table (populated by the previous run_scores pass).
    """
    from apthunt.scoring import baseline as bl

    for scorer in scorers:
        comp = getattr(scorer, "baseline_component", None)
        if not comp:
            continue
        try:
            rows = conn.execute(
                f"SELECT [{comp}] FROM listings "
                f"WHERE UPPER(status)='ACTIVE' AND [{comp}] IS NOT NULL"
            ).fetchall()
        except Exception as exc:
            print(f"  !! {scorer.name}: cannot read {comp} from listings: {exc}")
            continue
        vals = [float(r[0]) for r in rows]
        if len(vals) < 100:
            print(f"  !! {scorer.name}: only {len(vals)} listing samples — skipping")
            continue
        bl.store_baseline(
            conn, scorer.name, vals,
            reverse=bool(getattr(scorer, "baseline_reverse", False)),
            zero_is_perfect=bool(getattr(scorer, "baseline_zero_perfect", False)),
        )
        print(f"  ✓ {scorer.name}: baselined against {len(vals)} active-listing "
              f"buildings via '{comp}' [{min(vals):.3g} .. {max(vals):.3g}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", type=int, default=8000)
    ap.add_argument("--only", type=str, default=None)
    ap.add_argument("--from-listings", action="store_true",
                    help="baseline against active-listing buildings instead of "
                         "grid cells (building-level dimensions)")
    ap.add_argument("--db", type=str, default=DB_PATH)
    args = ap.parse_args()

    conn = get_connection(args.db)
    store = DataStore(conn)
    cache = BlockCache(conn)

    scorers = [
        CrimeScorer(store, cache),
        NoiseScorer(store, cache),
        PestScorer(store, cache),
        BuildingViolationsScorer(store, cache),
        ManagementScorer(store, cache),
        ParksScorer(store, cache),
        GreeneryScorer(store, cache),
        SchoolsScorer(store, cache),
        ShelterScorer(store, cache),
        ConvenienceScorer(store, cache),
        BedbugScorer(store, cache),
        StreetDangerScorer(store, cache),
        AirQualityScorer(store, cache),
        RoadExposureScorer(store, cache),
        DealScorer(),
        UnitAmenitiesScorer(),
    ]
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        scorers = [s for s in scorers if s.name in keep]

    if args.from_listings:
        build_from_listings(conn, scorers)
        conn.close()
        print("Baseline build (from listings) complete.")
        return

    print(f"Sampling {args.cells} residential cells ...")
    t0 = time.time()
    cells = residential_cells(conn, args.cells)
    listings = synthetic_listings(cells)
    print(f"  {len(listings)} cells in {time.time() - t0:.1f}s")

    for scorer in scorers:
        comp = getattr(scorer, "baseline_component", None)
        if not comp:
            print(f"  !! {scorer.name}: no baseline_component declared — skipping")
            continue
        t0 = time.time()
        results = scorer.score(conn, listings)
        raw = [r.components.get(comp) for r in results]
        raw = [v for v in raw if v is not None]
        if len(raw) < 100:
            print(f"  !! {scorer.name}: only {len(raw)} raw samples — skipping")
            continue
        bl.store_baseline(
            conn,
            scorer.name,
            raw,
            reverse=bool(getattr(scorer, "baseline_reverse", False)),
            zero_is_perfect=bool(getattr(scorer, "baseline_zero_perfect", False)),
        )
        lo, hi = min(raw), max(raw)
        print(
            f"  ✓ {scorer.name}: {len(raw)} samples of '{comp}' "
            f"[{lo:.3g} .. {hi:.3g}] in {time.time() - t0:.1f}s"
        )

    conn.close()
    print("Baseline build complete.")


if __name__ == "__main__":
    main()
