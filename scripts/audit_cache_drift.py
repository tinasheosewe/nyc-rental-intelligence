#!/usr/bin/env python3
"""
Cache-drift audit: catch formula-changed-but-version-not-bumped bugs.

The greenery incident (2026-07): the scorer's raw formula evolved while
its cache key stayed "greenery_v3", so listings scored through stale
cached stats got percentiles from a different formula era — Greenpoint
greenery read 41.8 where fresh compute said 66.0. The heatmap faithfully
reproduced the wrong numbers.

This audit samples cached geohash cells for every CURRENT cache key,
recomputes the block stats fresh (cache reads disabled), and compares the
scorer's baseline component. Material drift on multiple cells means the
cached generation no longer matches the code — bump the key and rescore.

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

    print(f"{'scorer':<16} {'cache key':<24} {'n':>3} "
          f"{'drift>1%':>8} {'max drift':>10}  verdict")
    flagged = []
    for scorer in scorers:
        key = discover_key(conn, scorer, store)
        if key is None:
            print(f"{scorer.name:<16} {'(no geohash cache)':<24}   -")
            continue
        comp = getattr(scorer, "baseline_component", None)
        rows = conn.execute(
            "SELECT geohash, data FROM block_cache WHERE source = ? "
            "AND length(geohash) = 7", (key,)
        ).fetchall()
        if not rows:
            print(f"{scorer.name:<16} {key:<24}   0        -          -  (empty)")
            continue
        sample = RNG.sample(rows, min(args.samples, len(rows)))

        import json
        n_drift, max_rel = 0, 0.0
        for gh, blob in sample:
            cached = json.loads(blob)
            if comp not in cached:
                continue
            lat, lon = geohash.decode(gh)
            lst = {"id": f"audit:{gh}", "lat": lat, "lon": lon,
                   "geohash": gh, "price": None, "beds": None,
                   "neighborhood": None, "borough": None,
                   "net_effective_price": None}
            res = scorer.score(conn, [lst])[0]
            fresh = (res.components or {}).get(comp)
            old = cached.get(comp)
            if fresh is None or old is None:
                if fresh != old:
                    n_drift += 1
                    max_rel = max(max_rel, 1.0)
                continue
            denom = max(abs(old), abs(fresh), 1e-9)
            rel = abs(fresh - old) / denom
            if rel > 0.01:
                n_drift += 1
            max_rel = max(max_rel, rel)
        verdict = "OK" if n_drift <= max(1, len(sample) // 10) else "STALE ⚠"
        print(f"{scorer.name:<16} {key:<24} {len(sample):>3} "
              f"{n_drift:>8} {max_rel:>9.1%}  {verdict}")
        if verdict != "OK":
            flagged.append((scorer.name, key))

    if flagged:
        print("\nSTALE caches (bump version + rescore):")
        for name, key in flagged:
            print(f"  {name}: {key}")
        sys.exit(1)
    print("\nAll current cache generations match their scorers.")
    conn.close()


if __name__ == "__main__":
    main()
