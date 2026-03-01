"""
Run all scoring pipelines.

Usage:
    python3 run_scores.py                     # all available scorers
    python3 run_scores.py --only deal         # deal scorer only
    python3 run_scores.py --only transit      # transit scorer only
    python3 run_scores.py --only flood_risk   # flood risk only
    python3 run_scores.py --only deal,transit # multiple scorers
    python3 run_scores.py --list              # list registered scorers
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from apthunt.db import get_connection, DB_PATH
from apthunt.scoring.engine import ScoringEngine
from apthunt.scoring.deal import DealScorer
from apthunt.scoring.transit import TransitScorer
from apthunt.scoring.flood_risk import FloodRiskScorer
from apthunt.scoring.crime import CrimeScorer
from apthunt.scoring.noise import NoiseScorer

from apthunt.data.data_store import DataStore
from apthunt.data.block_cache import BlockCache
from apthunt.data.transit_data import TransitData
from apthunt.scoring.building_violations import BuildingViolationsScorer
from apthunt.scoring.parks import ParksScorer
from apthunt.scoring.schools import SchoolsScorer
from apthunt.scoring.rent_stabilized import RentStabilizedScorer
from apthunt.scoring.management import ManagementScorer
from apthunt.scoring.amenity import AmenityScorer
from apthunt.scoring.shelter import ShelterScorer
from apthunt.scoring.pest import PestScorer


# Path to bundled GTFS stops.txt (download from MTA and place here)
STOPS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "stops.txt"
)


def build_scorers(
    conn,
    only: list[str] | None = None,
) -> list:
    """Instantiate all scorers (or a filtered subset)."""
    store = DataStore(conn)
    cache = BlockCache(conn)
    transit = TransitData(STOPS_PATH)


    all_scorers = {
        "deal": lambda: DealScorer(),
        "transit": lambda: TransitScorer(transit, cache),
        "flood_risk": lambda: FloodRiskScorer(store, cache),
        "crime": lambda: CrimeScorer(store, cache),
        "noise": lambda: NoiseScorer(store, cache),
        "building_violations": lambda: BuildingViolationsScorer(store, cache),
        "parks": lambda: ParksScorer(store, cache),
        "schools": lambda: SchoolsScorer(store, cache),
        "rent_stabilized": lambda: RentStabilizedScorer(store, cache),
        "management": lambda: ManagementScorer(store, cache),
        "amenity": lambda: AmenityScorer(store, cache),
        "shelter": lambda: ShelterScorer(store, cache),
        "pest": lambda: PestScorer(store, cache),
    }

    if only:
        unknown = set(only) - set(all_scorers)
        if unknown:
            print(f"Unknown scorers: {', '.join(unknown)}", file=sys.stderr)
            print(f"Available: {', '.join(all_scorers)}", file=sys.stderr)
            sys.exit(1)
        return [all_scorers[name]() for name in only]

    return [factory() for factory in all_scorers.values()]


def main():
    parser = argparse.ArgumentParser(description="Run scoring pipelines")
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated list of scorers to run (e.g. deal,transit)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available scorers and exit",
    )
    parser.add_argument(
        "--ids",
        type=str,
        default=None,
        help="Comma-separated listing IDs to score (default: all active)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DB_PATH,
        help="Path to the SQLite database",
    )
    args = parser.parse_args()

    if args.list:
        print("Available scorers:")
        print("  deal                 - Comp-set deal scoring (no external API)")
        print("  transit              - Subway station proximity (requires data/stops.txt)")
        print("  flood_risk           - FEMA flood zone flags (PLUTO via SODA API)")
        print("  crime                - NYPD complaint density")
        print("  noise                - 311 quality-of-life complaints")
        print("  building_violations  - DOB building violations per unit")
        print("  parks                - Proximity to parks/green space")
        print("  schools              - Public school quality")
        print("  management           - Owner/management company reputation (HPD complaints)")
        print("  amenity              - Nearby amenities (grocery, pharmacy, gym, etc.)")
        print("  shelter              - Proximity to homeless shelters/services & NYCHA projects")
        return

    conn = get_connection(args.db)
    only = args.only.split(",") if args.only else None
    scorers = build_scorers(conn, only)

    engine = ScoringEngine(conn)
    for scorer in scorers:
        engine.register(scorer)

    listing_ids = args.ids.split(",") if args.ids else None

    print(f"Running {len(scorers)} scorer(s): {', '.join(s.name for s in scorers)}")
    t0 = time.time()
    stats = engine.run(listing_ids)
    elapsed = time.time() - t0

    print(f"\nScoring complete in {elapsed:.1f}s")
    print(f"  Total listings scored: {stats['total_scored']}")
    for name, info in stats["scorers"].items():
        print(f"  {name}: {info['scored']} scored")

    conn.close()


if __name__ == "__main__":
    main()
