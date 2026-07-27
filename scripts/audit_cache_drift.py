#!/usr/bin/env python3
"""
Cache-drift audit: catch formula-changed-but-version-not-bumped bugs.

The greenery incident (2026-07): the scorer's raw formula evolved while
its cache key stayed "greenery_v3", so listings scored through stale
cached stats got percentiles from a different formula era — Greenpoint
greenery read 41.8 where fresh compute said 66.0. The heatmap faithfully
reproduced the wrong numbers.

This audit samples ACTIVE LISTINGS, recomputes each scorer's raw
baseline component fresh at the listing's own coordinates (cache reads
disabled), and compares against the component STORED on the listing row.
Material drift on multiple listings means the scores users see no longer
match current code — bump the key and rescore.

Why listings and not cached geohash rows: an earlier version decoded
cached geohash keys back to coordinates and recomputed there — but cache
entries are computed at the first-seen listing's coords within the
block, up to ~80 m from the decoded cell center. For smooth fields that
was fine; for sharp-gradient scorers (150 m noise kernel, severance,
park fractions) the position mismatch masqueraded as formula drift and
the audit cried stale on freshly rebuilt caches. Stored-vs-fresh at the
listing's exact coordinates is the invariant that actually matters.

A small residual tolerance remains: block stats are shared per
geohash-8 cell (~38 x 19 m), so a listing may carry a blockmate's stats
computed up to ~40 m away. The drift threshold (default 10%) plus the
per-scorer drift-count gate absorbs that.

Usage:  python3 scripts/audit_cache_drift.py [--samples 25]
"""

from __future__ import annotations

import argparse
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt import geohash
from apthunt.db import get_connection, DB_PATH
from apthunt.data.data_store import DataStore
from apthunt.data.block_cache import BlockCache
from apthunt.data.transit_data import TransitData

RNG = random.Random(42)


class NullCache:
    """Cache stand-in that forces fresh computation and swallows writes."""

    def get(self, gh, source):
        return None

    def put(self, gh, source, stats):
        pass

    def flush(self):
        pass


def build_audits(store, cache, null):
    """(cache_key, scorer instance w/ null cache, component) triples."""
    from apthunt.scoring.crime import CrimeScorer
    from apthunt.scoring.noise import NoiseScorer
    from apthunt.scoring.pest import PestScorer
    from apthunt.scoring.parks import ParksScorer
    from apthunt.scoring.greenery import GreeneryScorer
    from apthunt.scoring.convenience import ConvenienceScorer
    from apthunt.scoring.shelter import ShelterScorer
    from apthunt.scoring.street_danger import StreetDangerScorer
    from apthunt.scoring.road_exposure import RoadExposureScorer
    from apthunt.scoring.air_quality import AirQualityScorer
    from apthunt.scoring.transit import TransitScorer

    transit = TransitData(os.path.join(ROOT, "data", "stops.txt"))
    scorers = [
        CrimeScorer(store, null), NoiseScorer(store, null),
        PestScorer(store, null), ParksScorer(store, null),
        GreeneryScorer(store, null), ConvenienceScorer(store, null),
        ShelterScorer(store, null), StreetDangerScorer(store, null),
        RoadExposureScorer(store, null), AirQualityScorer(store, null),
        TransitScorer(transit, null),
    ]
    return scorers


def discover_key(conn, scorer, store):
    """Find which block_cache source key this scorer reads, by probing."""

    class SpyCache(NullCache):
        def __init__(self):
            self.sources = set()

        def get(self, gh, source):
            self.sources.add(source)
            return None

    spy = SpyCache()
    real = scorer._cache
    scorer._cache = spy
    lst = {"id": "spy", "lat": 40.72, "lon": -73.95,
           "geohash": geohash.encode(40.72, -73.95),
           "price": None, "beds": None, "neighborhood": None,
           "borough": None, "net_effective_price": None}
    try:
        scorer.score(conn, [lst])
    except Exception:
        pass
    finally:
        scorer._cache = real
    # Geohash-cell key only (building dims also probe bbl: keys)
    for s in spy.sources:
        if not s.startswith("bbl"):
            return s
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=25)
    ap.add_argument("--tolerance", type=float, default=0.10,
                    help="relative drift above which a listing counts as "
                         "drifted (absorbs ~40m block-sharing residue)")
    args = ap.parse_args()

    conn = get_connection(DB_PATH)
    store = DataStore(conn)
    cache = BlockCache(conn)
    null = NullCache()
    try:
        from apthunt.data.spatial_index import activate_fast_path
        activate_fast_path(conn, store)
    except Exception as exc:
        print(f"fast path unavailable ({exc}); audit will be slow")

    scorers = build_audits(store, cache, null)
    listing_cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}

    print(f"{'scorer':<16} {'component':<26} {'n':>3} "
          f"{'drift>tol':>9} {'max drift':>10}  verdict")
    flagged = []
    for scorer in scorers:
        comp = getattr(scorer, "baseline_component", None)
        if comp is None or comp not in listing_cols:
            print(f"{scorer.name:<16} {'(no stored component)':<26}   -")
            continue
        rows = conn.execute(
            f"SELECT id, lat, lon, [{comp}] FROM listings "
            f"WHERE status='ACTIVE' AND [{comp}] IS NOT NULL "
            "AND lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchall()
        if not rows:
            print(f"{scorer.name:<16} {comp:<26}   0         -          -  (empty)")
            continue
        sample = RNG.sample(rows, min(args.samples, len(rows)))

        n_drift, max_rel = 0, 0.0
        for lid, lat, lon, stored in sample:
            lst = {"id": f"audit:{lid}", "lat": lat, "lon": lon,
                   "geohash": geohash.encode(lat, lon, precision=8),
                   "price": None, "beds": None, "neighborhood": None,
                   "borough": None, "net_effective_price": None}
            res = scorer.score(conn, [lst])[0]
            fresh = (res.components or {}).get(comp)
            if fresh is None or stored is None:
                if fresh != stored:
                    n_drift += 1
                    max_rel = max(max_rel, 1.0)
                continue
            try:
                stored_f = float(stored)
            except (TypeError, ValueError):
                continue
            denom = max(abs(stored_f), abs(fresh), 1e-9)
            rel = abs(fresh - stored_f) / denom
            if rel > args.tolerance:
                n_drift += 1
            max_rel = max(max_rel, rel)
        verdict = "OK" if n_drift <= max(1, len(sample) // 5) else "STALE ⚠"
        print(f"{scorer.name:<16} {comp:<26} {len(sample):>3} "
              f"{n_drift:>9} {max_rel:>9.1%}  {verdict}")
        if verdict != "OK":
            flagged.append((scorer.name, comp))

    if flagged:
        print("\nSTALE scores (formula changed since last rescore — "
              "bump cache key + rescore):")
        for name, compname in flagged:
            print(f"  {name}: {compname}")
        sys.exit(1)
    print("\nAll stored listing components match current scorer code.")
    conn.close()


if __name__ == "__main__":
    main()
