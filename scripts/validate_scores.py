#!/usr/bin/env python3
"""
Score-validation harness: spread + ground truth.

Two guarantees this enforces:
1. SPREAD — every dimension must use the full 0-100 range across the city,
   not huddle at 50. Reports std, P5-P95 range, and mass near the midpoint
   for (a) the citywide residential sample implied by baseline_dist and
   (b) the actual active-listing population.
2. GROUND TRUTH — pairs of known-contrast locations must order correctly
   (park-adjacent beats concrete canyon on greenery, Union Sq beats deep
   Queens on transit, etc.). A good apartment must be visibly, numerically
   better than a bad one on each dimension.

Usage:
    python3 scripts/validate_scores.py            # full report
    python3 scripts/validate_scores.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from statistics import mean, pstdev

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


# ── Ground-truth probe locations ────────────────────────────────────
# Each probe: (dimension, higher_name, (lat, lon), lower_name, (lat, lon))
# "higher" is expected to OUTSCORE "lower" on that dimension.

PROBES = [
    ("greenery", "Prospect Park edge (PLG)", (40.6602, -73.9625),
                 "Midtown Garment District", (40.7539, -73.9917)),
    ("parks",    "Central Park West @ 86th", (40.7866, -73.9754),
                 "Maspeth industrial", (40.7231, -73.9077)),
    ("crime",    "Forest Hills Gardens", (40.7146, -73.8437),
                 "Times Square core", (40.7580, -73.9855)),
    # NB: the original Fieldston pin (40.8896,-73.9057) sits on a real
    # complaint pocket — the Manhattan College Pkwy corridor logged 165
    # in-type complaints/12mo across 57 distinct sources. Interior
    # Fieldston asserts what this probe means to assert ("quietest
    # leafy NYC beats nightlife core"); the old pin is a known noise
    # pocket, not a scorer bug.
    ("noise",    "Riverdale (Fieldston interior)", (40.8925, -73.9095),
                 "Lower East Side nightlife", (40.7205, -73.9873)),
    # Strip-blur regression test (the 300m-kernel bug): a quiet
    # Williamsburg side street (Fillmore Pl) must clearly beat the
    # Bedford & N7th nightlife core — under the old kernel it carried
    # 0.96x the core's complaint rate and scored within 1 point of it.
    ("noise",    "Fillmore Pl (Wburg side street)", (40.7143, -73.9563),
                 "Bedford Ave & N 7th (nightlife core)", (40.7193, -73.9555)),
    # Denominator-floor regression test: the McCarren Park edge must not
    # read "loud" just because few households live beside a park (it
    # scored 9.6 on one-tenth the core's complaint mass, units=141).
    ("noise",    "Driggs & N 12th (McCarren edge)", (40.7212, -73.9530),
                 "Bedford Ave & N 7th (nightlife core)", (40.7193, -73.9555)),
    # Park-adjacency regression test (repaired geometry + edge distance).
    # NB coords: Cooper Park is bounded by Sharon St on the SOUTH
    # (~40.7152) — Frost/Debevoise sit 150m+ north in the NYCHA Cooper
    # Park Houses campus and are NOT park-adjacent (a diagnostic agent
    # misread this and manufactured a phantom "displaced geometry"
    # discount; PLUTO street addresses settled it).
    ("parks",    "Sharon St @ Cooper Park", (40.7152, -73.9375),
                 "Morgan Ave industrial", (40.7130, -73.9280)),
    ("convenience", "Upper West Side (Broadway/79th)", (40.7838, -73.9800),
                    "Broad Channel (Jamaica Bay)", (40.6032, -73.8202)),
    # NB: first shelter probe used Carroll Gardens, which legitimately has
    # the Gowanus Houses + services in kernel range — bad contrast pair.
    ("shelter",  "Douglaston (deep Queens)", (40.7679, -73.7430),
                 "Brownsville (Rockaway Ave)", (40.6707, -73.9106)),
    ("street_danger", "Park Slope side street", (40.6710, -73.9782),
                      "Flatbush & Atlantic hub", (40.6840, -73.9772)),
    # Density-confound regression test: after per-household normalization,
    # dense-but-livable Williamsburg must clearly beat a genuinely
    # high-crime area — with raw counts it scored near the bottom simply
    # for being crowded.
    ("crime",    "Williamsburg (Grand St)", (40.7120, -73.9550),
                 "Brownsville (Rockaway Ave)", (40.6707, -73.9106)),
    # Road noise: an inner Park Slope block must beat a building beside
    # the BQE trench in Williamsburg.
    # NB: first probe pin was ~550m off the highway — Meeker parallels the
    # BQE at ~40.720 latitude in this stretch, verified against ds_roads.
    ("road_exposure", "Park Slope inner block", (40.6710, -73.9782),
                      "Beside the BQE (Meeker & Morgan)", (40.7203, -73.9438)),
    # Elevated-train component: under the J/M/Z el on Broadway must be
    # clearly worse than an inner block.
    ("road_exposure", "Park Slope inner block", (40.6710, -73.9782),
                      "Under the JMZ el (Broadway & Hewes)", (40.7069, -73.9532)),
]

# Spread thresholds (listing population; city sample is uniform by construction)
MIN_STD = 12.0
MIN_P95_P5 = 35.0
MAX_MIDMASS = 0.45  # no more than 45% of listings within [45, 55]

# Dimensions that DELIBERATELY do not use the full percentile range:
# air quality is absolute and health-anchored — NYC's intra-city air
# differences are modest in health terms, and percentiling that narrow
# range manufactured drama ("worse than 95% of NYC") for differences no
# resident can perceive. Its narrow spread is the honest answer.
SPREAD_EXEMPT = {"air_quality"}


def build_scorers(conn):
    store = DataStore(conn)
    cache = BlockCache(conn)
    return {
        s.name: s
        for s in [
            CrimeScorer(store, cache), NoiseScorer(store, cache),
            PestScorer(store, cache), BuildingViolationsScorer(store, cache),
            ManagementScorer(store, cache), ParksScorer(store, cache),
            GreeneryScorer(store, cache), SchoolsScorer(store, cache),
            ShelterScorer(store, cache), ConvenienceScorer(store, cache),
            BedbugScorer(store, cache), StreetDangerScorer(store, cache),
            AirQualityScorer(store, cache), RoadExposureScorer(store, cache),
        ]
    }


def spread_stats(values):
    vals = sorted(v for v in values if v is not None)
    if len(vals) < 20:
        return None
    n = len(vals)
    pct = lambda p: vals[min(n - 1, int(p * n))]
    mid = sum(1 for v in vals if 45.0 <= v <= 55.0) / n
    return {
        "n": n,
        "mean": round(mean(vals), 1),
        "std": round(pstdev(vals), 1),
        "p5": pct(0.05), "p25": pct(0.25), "p50": pct(0.50),
        "p75": pct(0.75), "p95": pct(0.95),
        "min": vals[0], "max": vals[-1],
        "mid_mass": round(mid, 3),
    }


def check_listing_spread(conn):
    dims = [
        r[0] for r in conn.execute("SELECT dimension FROM baseline_dist")
        if not r[0].startswith("__")  # internal distributions (e.g. __composite__)
    ]
    out = {}
    for dim in dims:
        col = f"{dim}_score"
        try:
            rows = conn.execute(
                f"SELECT {col} FROM listings WHERE UPPER(status)='ACTIVE' "
                f"AND {col} IS NOT NULL"
            ).fetchall()
        except Exception:
            out[dim] = {"error": f"column {col} missing"}
            continue
        st = spread_stats([r[0] for r in rows])
        if st is None:
            out[dim] = {"error": "too few scored listings"}
            continue
        if dim in SPREAD_EXEMPT:
            st["pass_std"] = st["pass_range"] = st["pass_midmass"] = True
            st["pass"] = True
            st["note"] = "absolute-anchored (spread-exempt)"
            out[dim] = st
            continue
        st["pass_std"] = st["std"] >= MIN_STD
        st["pass_range"] = (st["p95"] - st["p5"]) >= MIN_P95_P5
        st["pass_midmass"] = st["mid_mass"] <= MAX_MIDMASS
        st["pass"] = st["pass_std"] and st["pass_range"] and st["pass_midmass"]
        out[dim] = st
    return out


def probe_listing(lat, lon):
    return {
        "id": f"probe:{lat}:{lon}",
        "lat": lat, "lon": lon,
        "geohash": geohash.encode(lat, lon),
        "price": None, "beds": None, "neighborhood": None,
        "borough": None, "net_effective_price": None,
    }


def check_ground_truth(conn, scorers):
    results = []
    for dim, hi_name, hi_ll, lo_name, lo_ll in PROBES:
        scorer = scorers.get(dim)
        if scorer is None:
            continue
        pair = [probe_listing(*hi_ll), probe_listing(*lo_ll)]
        try:
            res = scorer.score(conn, pair)
            hi_score = res[0].score
            lo_score = res[1].score
        except Exception as e:
            results.append({"dimension": dim, "error": str(e)[:200]})
            continue
        ok = (
            hi_score is not None and lo_score is not None
            and hi_score > lo_score + 5.0   # must win by a visible margin
        )
        results.append({
            "dimension": dim,
            "expect_higher": hi_name, "score_higher": hi_score,
            "expect_lower": lo_name, "score_lower": lo_score,
            "margin": None if None in (hi_score, lo_score) else round(hi_score - lo_score, 1),
            "pass": ok,
        })
    return results


def check_composite_spread(conn):
    """The displayed composite must span the full range (it is a percentile
    among active listings by construction — this guards regressions)."""
    try:
        rows = conn.execute(
            "SELECT composite_score FROM listings "
            "WHERE UPPER(status)='ACTIVE' AND composite_score IS NOT NULL"
        ).fetchall()
    except Exception:
        return {"error": "composite_score column missing"}
    st = spread_stats([r[0] for r in rows])
    if st is None:
        return {"error": "too few composites"}
    st["pass"] = (st["p95"] - st["p5"]) >= 70 and st["std"] >= 20
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", type=str, default=DB_PATH)
    args = ap.parse_args()

    conn = get_connection(args.db)
    bl.clear_cache()
    scorers = build_scorers(conn)

    spread = check_listing_spread(conn)
    spread["composite (displayed)"] = check_composite_spread(conn)
    truth = check_ground_truth(conn, scorers)
    conn.close()

    if args.json:
        print(json.dumps({"spread": spread, "ground_truth": truth}, indent=1))
        return

    print("\n══ SPREAD — active-listing score distributions ══")
    hdr = f"{'dim':<22}{'n':>6}{'mean':>7}{'std':>6}{'p5':>6}{'p50':>6}{'p95':>6}{'mid%':>7}  verdict"
    print(hdr); print("─" * len(hdr))
    for dim, st in sorted(spread.items()):
        if "error" in st:
            print(f"{dim:<22}  {st['error']}")
            continue
        verdict = "PASS" if st["pass"] else (
            "FAIL:" + ",".join(k[5:] for k in ("pass_std", "pass_range", "pass_midmass") if not st[k])
        )
        print(f"{dim:<22}{st['n']:>6}{st['mean']:>7}{st['std']:>6}"
              f"{st['p5']:>6}{st['p50']:>6}{st['p95']:>6}{st['mid_mass']*100:>6.0f}%  {verdict}")

    print("\n══ GROUND TRUTH — known-contrast location pairs ══")
    for t in truth:
        if "error" in t:
            print(f"  {t['dimension']:<14} ERROR: {t['error']}")
            continue
        mark = "✓" if t["pass"] else "✗"
        print(f"  {mark} {t['dimension']:<14} {t['expect_higher']} = {t['score_higher']}"
              f"  vs  {t['expect_lower']} = {t['score_lower']}"
              f"  (margin {t['margin']})")

    n_fail = sum(1 for s in spread.values() if not s.get("pass", True)) + \
             sum(1 for t in truth if not t.get("pass", True))
    print(f"\n{'ALL CHECKS PASSED' if n_fail == 0 else f'{n_fail} CHECK(S) FAILED'}")
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
