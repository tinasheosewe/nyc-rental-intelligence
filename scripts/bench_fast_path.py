#!/usr/bin/env python3
"""
Correctness harness + micro-benchmark for the Tier-1 spatial fast path.

STRICTLY READ-ONLY: opens the DB with a ``mode=ro`` URI, never writes,
never runs scorers end-to-end (safe to run while a scoring chain is in
flight against the same DB).

For N random active-listing coordinates it compares, per dataset:

    - ``DataStore.query_circle`` (pure SQLite, the correctness
      reference) vs ``MemoryIndex.query_circle`` — row counts must
      match within ±1 (boundary ties) and _dist_m sums within 0.5%;
    - ``RoadExposureScorer._occluding_rows`` (SQLite loop) vs
      ``BlockerRaster.occluding_rows`` (reported, not asserted — the
      raster is a documented 10 m-cell approximation);
    - ``utils._path_severance_penalty_m_sql`` vs
      ``SeveranceRaster.path_penalty_m`` (reported, not asserted).

Then it micro-benchmarks median per-call latency on both paths and
times a simulated 100-cell crime scoring loop (crime circle + kernel
accumulation + PLUTO unit-kernel denominator, the CrimeScorer per-block
recipe) to project the full-run speedup.

KEYED SECTION (building dimensions): for ~30 random listing BBLs it
compares ``DataStore.rows_by_key`` answered by ``KeyedMaps`` vs the
generic SQLite fallback for every per-building lookup the four
building-dimension scorers use — row counts must match EXACTLY and a
per-column checksum must agree — then micro-benchmarks per-lookup
latency both ways and times a simulated 100-building
building_violations stats loop (DOB fetch + HPD fetch + permit count).

Usage:
    .venv/bin/python scripts/bench_fast_path.py [--coords 50] [--db PATH]
                                                [--bbls 30] [--keyed-only]
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sqlite3
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt.db import DB_PATH
from apthunt.data.data_store import DataStore
from apthunt.data.spatial_index import (
    BlockerRaster,
    KeyedMaps,
    MemoryIndex,
    SeveranceRaster,
)
from apthunt.scoring.road_exposure import HWY_CLASSES, RoadExposureScorer
from apthunt.scoring.utils import (
    BUILDING_MATCH_MAX_M,
    _path_severance_penalty_m_sql,
    find_nearest_row,
    normalize_bbl,
    parse_bbl,
)

SEED = 20260726

# (dataset, radius_m, select, lat_col, lon_col) — mirrors the real
# scorer call signatures (CrimeScorer, NoiseScorer, occlusion probe,
# kernel_weighted_units, RoadExposureScorer).
QUERY_SPECS = [
    ("crime", 400.0, "law_cat_cd,cmplnt_fr_dt,latitude,longitude",
     "latitude", "longitude"),
    ("noise", 300.0, "complaint_type,created_date,latitude,longitude",
     "latitude", "longitude"),
    ("pluto", 18.0, "numfloors", "latitude", "longitude"),
    ("pluto", 400.0, "unitsres", "latitude", "longitude"),
    ("roads", 1000.0, "road_class,lat,lon", "lat", "lon"),
]


def open_ro(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def sample_coords(conn, n: int) -> list:
    rows = conn.execute(
        "SELECT lat, lon FROM listings "
        "WHERE UPPER(status)='ACTIVE' AND lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()
    coords = sorted({(float(r["lat"]), float(r["lon"])) for r in rows})
    rng = random.Random(SEED)
    if len(coords) > n:
        coords = rng.sample(coords, n)
    return coords


def median_ms(fn, args_list, repeat: int = 1) -> float:
    times = []
    for args in args_list:
        t0 = time.perf_counter()
        for _ in range(repeat):
            fn(*args)
        times.append((time.perf_counter() - t0) / repeat * 1000.0)
    return statistics.median(times)


# ---------------------------------------------------------------------------
# Keyed section (building dimensions)
# ---------------------------------------------------------------------------

_BORO_NAMES = {"1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN",
               "4": "QUEENS", "5": "STATEN ISLAND"}

# HPD-violation columns the scorer requests under the current schema.
_HPD_VIOL_COLS = ["class", "inspectiondate", "novdescription",
                  "certifieddate", "violationstatus"]

# One row per scorer lookup site:
# (label, dataset, key_expr, columns, probe(building)->key|None, checksum_col)
# columns=[] → key columns only (count-style callers).
KEYED_SPECS = [
    ("dob_violations", "dob_violations", "boro,block,lot",
     ["violation_type"],
     lambda b: (b["boro"], b["block"], b["lot"]),
     "violation_type"),
    ("hpd_violations", "hpd_violations", "boroid,block,lot",
     list(_HPD_VIOL_COLS),
     lambda b: (b["boro"], str(int(b["block"])), str(int(b["lot"]))),
     "inspectiondate"),
    ("dob_permits", "dob_permits", "borough,block,lot",
     [],
     lambda b: (_BORO_NAMES.get(b["boro"], ""), b["block"], b["lot"]),
     "block"),
    ("pluto(owner)", "pluto", "ownername",
     ["bbl", "unitsres"],
     lambda b: b["owner"] or None,
     "unitsres"),
    ("hpd_complaints", "hpd_complaints", "bbl",
     ["major_category", "minor_category", "received_date"],
     lambda b: b["bbl"],
     "received_date"),
    ("evictions", "evictions", "bbl",
     ["executed_date"],
     lambda b: b["bbl"],
     "executed_date"),
    ("hpd_litigations", "hpd_litigations", "boroid,block,lot",
     [],
     lambda b: (b["boro"], str(int(b["block"])), str(int(b["lot"]))),
     "block"),
    ("bedbug_reporting", "bedbug_reporting", "bbl",
     ["of_dwelling_units", "infested_dwelling_unit_count",
      "re_infested_dwelling_unit", "filing_date"],
     lambda b: b["bbl"],
     "infested_dwelling_unit_count"),
]


def resolve_buildings(store, coords, n: int) -> list:
    """Resolve listing coordinates to unique PLUTO buildings the same
    way the scorers do (bbox + nearest lot, 40 m attribution guard)."""
    out, seen = [], set()
    for lat, lon in coords:
        rows = store.query_bbox("pluto", lat, lon, delta=0.0015)
        nearest = find_nearest_row(
            rows, lat, lon, max_dist_m=BUILDING_MATCH_MAX_M)
        if nearest is None or not nearest.get("bbl"):
            continue
        try:
            boro, block, lot = parse_bbl(nearest["bbl"])
        except (ValueError, IndexError):
            continue
        bbl = normalize_bbl(nearest["bbl"])
        if bbl in seen:
            continue
        seen.add(bbl)
        out.append({
            "bbl": bbl, "boro": boro, "block": block, "lot": lot,
            "owner": (nearest.get("ownername") or "").strip(),
        })
        if len(out) >= n:
            break
    return out


def _checksum(rows, col) -> float:
    """Order-insensitive checksum: numeric values summed, text values
    contribute their length — identical on both paths by construction."""
    total = 0.0
    for r in rows:
        v = r.get(col)
        if v is None:
            continue
        try:
            total += float(v)
        except (TypeError, ValueError):
            total += float(len(str(v)))
    return total


def _existing_cols(conn, dataset, cols):
    """Filter a requested column list to what the table has today (same
    schema-drift tolerance activate_fast_path applies)."""
    if not cols:
        return cols
    have = {r[1] for r in conn.execute(f"PRAGMA table_info([ds_{dataset}])")}
    return [c for c in cols if c in have]


def keyed_section(conn, store_sql, coords, failures, n_bbls: int) -> None:
    """Parity + micro-bench for the KeyedMaps fast path."""
    keyed = KeyedMaps(conn)
    store_fast = DataStore(conn)
    store_fast.attach_fast_path(keyed_maps=keyed)

    # ── preload (timed) ──────────────────────────────────────────────
    print("\nKeyed-map preloads (read-only)")
    hdr = f"{'dataset':<18} {'key_expr':<18} {'rows':>10} {'keys':>10} {'build_s':>8}"
    print(hdr)
    print("-" * len(hdr))
    for label, ds, key_expr, cols, _probe, _ck in KEYED_SPECS:
        load_cols = _existing_cols(conn, ds, list(cols))
        t0 = time.perf_counter()
        ok = keyed.load_keyed(ds, key_expr, load_cols)
        secs = time.perf_counter() - t0
        if ok:
            entry = keyed._entries[(ds, key_expr)]
            print(f"{ds:<18} {key_expr:<18} {entry.n_rows:>10,} "
                  f"{entry.n_keys:>10,} {secs:>8.2f}")
        else:
            print(f"{ds:<18} {key_expr:<18} {'LOAD FAILED':>10} "
                  f"{'-':>10} {secs:>8.2f}")
            failures.append(f"keyed preload failed: {ds}/{key_expr}")

    buildings = resolve_buildings(store_sql, coords, max(n_bbls, 100))
    probe_set = buildings[:n_bbls]
    print(f"\n{len(probe_set)} random listing BBLs for parity "
          f"({len(buildings)} resolved for the loop)")

    # ── parity + per-lookup latency ──────────────────────────────────
    print("\nrows_by_key parity (KeyedMaps vs SQLite fallback) "
          "+ median per-lookup latency")
    hdr = (f"{'lookup':<18} {'keys':>5} {'rows_sql':>9} {'rows_fast':>9} "
           f"{'sql_ms':>9} {'fast_ms':>9} {'speedup':>9}  result")
    print(hdr)
    print("-" * len(hdr))
    for label, ds, key_expr, cols, probe, ck_col in KEYED_SPECS:
        use_cols = _existing_cols(conn, ds, list(cols)) if cols else cols
        n_keys = tot_sql = tot_fast = 0
        t_sql, t_fast = [], []
        ok = True
        for b in probe_set:
            key = probe(b)
            if key is None:
                continue
            n_keys += 1
            t0 = time.perf_counter()
            f_rows = store_fast.rows_by_key(ds, key_expr, key,
                                            columns=use_cols)
            t_fast.append((time.perf_counter() - t0) * 1000.0)
            t0 = time.perf_counter()
            s_rows = store_sql.rows_by_key(ds, key_expr, key,
                                           columns=use_cols)
            t_sql.append((time.perf_counter() - t0) * 1000.0)
            tot_sql += len(s_rows)
            tot_fast += len(f_rows)
            if len(s_rows) != len(f_rows):
                ok = False
                failures.append(
                    f"keyed {label} key={key!r}: row count "
                    f"{len(s_rows)} vs {len(f_rows)}")
                continue
            cs, cf = _checksum(s_rows, ck_col), _checksum(f_rows, ck_col)
            if abs(cs - cf) > max(1e-6, 1e-9 * max(abs(cs), abs(cf))):
                ok = False
                failures.append(
                    f"keyed {label} key={key!r}: checksum({ck_col}) "
                    f"{cs!r} vs {cf!r}")
        m_sql = statistics.median(t_sql) if t_sql else float("nan")
        m_fast = statistics.median(t_fast) if t_fast else float("nan")
        speed = (m_sql / m_fast) if t_fast and m_fast > 0 else float("inf")
        print(f"{label:<18} {n_keys:>5} {tot_sql:>9} {tot_fast:>9} "
              f"{m_sql:>9.3f} {m_fast:>9.4f} {speed:>8.0f}x  "
              f"{'PASS' if ok else 'FAIL'}")

    # ── simulated 100-building building_violations stats loop ────────
    hpd_cols = _existing_cols(conn, "hpd_violations", list(_HPD_VIOL_COLS))
    loop = buildings[:100]

    def bviol_stats(store, b):
        """The three per-building fetches of
        BuildingViolationsScorer._build_stats (aggregation elided)."""
        dob = store.rows_by_key(
            "dob_violations", "boro,block,lot",
            (b["boro"], b["block"], b["lot"]),
            columns=["violation_type"])
        hpd = store.rows_by_key(
            "hpd_violations", "boroid,block,lot",
            (b["boro"], str(int(b["block"])), str(int(b["lot"]))),
            columns=hpd_cols)
        permits = len(store.rows_by_key(
            "dob_permits", "borough,block,lot",
            (_BORO_NAMES.get(b["boro"], ""), b["block"], b["lot"]),
            columns=[]))
        return len(dob), len(hpd), permits

    bviol_stats(store_fast, loop[0])            # warm (maps already built)
    t0 = time.perf_counter()
    for b in loop:
        bviol_stats(store_fast, b)
    t_fast_loop = time.perf_counter() - t0
    t0 = time.perf_counter()
    for b in loop:
        bviol_stats(store_sql, b)
    t_sql_loop = time.perf_counter() - t0

    per_sql = t_sql_loop / len(loop)
    per_fast = t_fast_loop / len(loop)
    print(f"\nSimulated building_violations stats loop "
          f"({len(loop)} buildings): sqlite {t_sql_loop:.2f}s vs "
          f"fast {t_fast_loop:.3f}s → "
          f"{t_sql_loop / t_fast_loop if t_fast_loop else float('inf'):.0f}x "
          f"({per_sql * 1000:.0f} ms vs {per_fast * 1000:.2f} ms per building)")
    n_coords = conn.execute(
        "SELECT COUNT(DISTINCT ROUND(lat,5) || ',' || ROUND(lon,5)) "
        "FROM listings WHERE UPPER(status)='ACTIVE' "
        "AND lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchone()[0]
    print(f"Projected over {n_coords:,} unique active-listing buildings: "
          f"sqlite {per_sql * n_coords:.0f}s vs fast "
          f"{per_fast * n_coords:.1f}s (this dimension)")


def finish(failures) -> None:
    print()
    if failures:
        print(f"PARITY FAILURES ({len(failures)}):")
        for f in failures[:20]:
            print(f"  - {f}")
        sys.exit(1)
    print("All parity assertions PASSED.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coords", type=int, default=50)
    ap.add_argument("--db", type=str, default=DB_PATH)
    ap.add_argument("--bbls", type=int, default=30,
                    help="random listing BBLs for the keyed parity section")
    ap.add_argument("--keyed-only", action="store_true",
                    help="run only the KeyedMaps section (skip rasters/KD)")
    args = ap.parse_args()

    conn = open_ro(args.db)
    store_sql = DataStore(conn)    # reference: no fast path attached

    if args.keyed_only:
        failures = []
        keyed_coords = sample_coords(conn, 400)
        keyed_section(conn, store_sql, keyed_coords, failures, args.bbls)
        finish(failures)
        conn.close()
        return

    store_fast = DataStore(conn)
    index = MemoryIndex(conn)
    store_fast.attach_memory_index(index)

    print("Building rasters (read-only) ...")
    blocker = BlockerRaster(conn)
    severance = SeveranceRaster(conn)
    print(f"  BlockerRaster   {blocker.build_seconds:6.1f}s")
    print(f"  SeveranceRaster {severance.build_seconds:6.1f}s")

    coords = sample_coords(conn, args.coords)
    print(f"\n{len(coords)} random active-listing coordinates (seed {SEED})")

    failures = []

    # ── query_circle parity ──────────────────────────────────────────
    print("\nquery_circle parity (SQLite reference vs MemoryIndex)")
    hdr = (f"{'dataset':<10} {'r_m':>6} {'rows_sql':>9} {'rows_fast':>9} "
           f"{'max|Δrows|':>10} {'Σdist Δ%':>9}  result")
    print(hdr)
    print("-" * len(hdr))
    for ds, radius, select, lat_col, lon_col in QUERY_SPECS:
        tot_sql = tot_fast = 0
        max_row_diff = 0
        max_pct = 0.0
        ok = True
        for lat, lon in coords:
            s_rows = store_sql.query_circle(
                ds, lat=lat, lon=lon, radius_m=radius, select=select,
                lat_col=lat_col, lon_col=lon_col)
            f_rows = index.query_circle(
                ds, lat, lon, radius, select_cols=select,
                lat_col=lat_col, lon_col=lon_col)
            if f_rows is None:
                ok = False
                failures.append(f"{ds}@{radius:.0f}m: index declined to serve")
                break
            tot_sql += len(s_rows)
            tot_fast += len(f_rows)
            diff = abs(len(s_rows) - len(f_rows))
            max_row_diff = max(max_row_diff, diff)
            if diff > 1:
                ok = False
                failures.append(
                    f"{ds}@{radius:.0f}m ({lat:.5f},{lon:.5f}): "
                    f"row count {len(s_rows)} vs {len(f_rows)}")
            s_sum = sum(r["_dist_m"] for r in s_rows)
            f_sum = sum(r["_dist_m"] for r in f_rows)
            if s_sum or f_sum:
                pct = abs(s_sum - f_sum) / max(s_sum, f_sum) * 100.0
                max_pct = max(max_pct, pct)
                if pct > 0.5:
                    ok = False
                    failures.append(
                        f"{ds}@{radius:.0f}m ({lat:.5f},{lon:.5f}): "
                        f"dist sum {s_sum:.1f} vs {f_sum:.1f} ({pct:.3f}%)")
        print(f"{ds:<10} {radius:>6.0f} {tot_sql:>9} {tot_fast:>9} "
              f"{max_row_diff:>10} {max_pct:>8.4f}%  "
              f"{'PASS' if ok else 'FAIL'}")

    # ── occlusion agreement ──────────────────────────────────────────
    scorer = RoadExposureScorer(store_sql, None)   # SQLite reference path
    sightlines = []
    for lat, lon in coords:
        rows = store_sql.query_circle(
            "roads", lat=lat, lon=lon, radius_m=1000.0,
            select="road_class,lat,lon", lat_col="lat", lon_col="lon")
        hwy = [r for r in rows if r.get("road_class") in HWY_CLASSES]
        if hwy:
            nearest = min(hwy, key=lambda r: r["_dist_m"])
            sightlines.append(
                (lat, lon, float(nearest["lat"]), float(nearest["lon"])))
    occ_pairs = [
        (scorer._occluding_rows(*sl), blocker.occluding_rows(*sl))
        for sl in sightlines
    ]
    if occ_pairs:
        exact = sum(1 for s, f in occ_pairs if s == f)
        capped = sum(1 for s, f in occ_pairs if min(s, 2) == min(f, 2))
        diffs = [abs(s - f) for s, f in occ_pairs]
        print(f"\noccluding_rows agreement over {len(occ_pairs)} "
              f"listing→highway sightlines (approximation, not asserted):")
        print(f"  exact match {exact}/{len(occ_pairs)} "
              f"({100.0 * exact / len(occ_pairs):.0f}%), "
              f"effect-equal (rows capped at 2, as the scorer uses them) "
              f"{capped}/{len(occ_pairs)} "
              f"({100.0 * capped / len(occ_pairs):.0f}%), "
              f"mean |Δrows| {statistics.mean(diffs):.2f}, "
              f"max {max(diffs)}")

    # ── severance agreement ──────────────────────────────────────────
    paths = [(lat, lon, lat + 0.004, lon + 0.004) for lat, lon in coords]
    sev_pairs = [
        (_path_severance_penalty_m_sql(store_sql, *p),
         severance.path_penalty_m(*p))
        for p in paths
    ]
    exact = sum(1 for s, f in sev_pairs if s == f)
    diffs = [abs(s - f) for s, f in sev_pairs]
    print(f"severance penalty agreement over {len(sev_pairs)} ~630m paths "
          f"(approximation, not asserted):")
    print(f"  exact match {exact}/{len(sev_pairs)} "
          f"({100.0 * exact / len(sev_pairs):.0f}%), "
          f"mean |Δpenalty| {statistics.mean(diffs):.1f} m, "
          f"max {max(diffs):.0f} m")

    # ── micro-benchmark: per-call latency ────────────────────────────
    print("\nMedian per-call latency (ms)")
    hdr = f"{'query':<22} {'sqlite':>9} {'fast':>9} {'speedup':>9}"
    print(hdr)
    print("-" * len(hdr))
    bench_rows = []
    for ds, radius, select, lat_col, lon_col in QUERY_SPECS:
        sql_args = [
            (ds, lat, lon, radius, select, lat_col, lon_col)
            for lat, lon in coords
        ]

        def _sql(ds, lat, lon, r, sel, lc, oc):
            store_sql.query_circle(ds, lat=lat, lon=lon, radius_m=r,
                                   select=sel, lat_col=lc, lon_col=oc)

        def _fast(ds, lat, lon, r, sel, lc, oc):
            index.query_circle(ds, lat, lon, r, select_cols=sel,
                               lat_col=lc, lon_col=oc)

        _fast(*sql_args[0])          # warm (load once, outside timing)
        m_sql = median_ms(_sql, sql_args)
        m_fast = median_ms(_fast, sql_args, repeat=3)
        label = f"{ds}@{radius:.0f}m"
        bench_rows.append((label, m_sql, m_fast))
        print(f"{label:<22} {m_sql:>9.2f} {m_fast:>9.3f} "
              f"{m_sql / m_fast if m_fast else float('inf'):>8.0f}x")

    if sightlines:
        m_sql = median_ms(lambda *sl: scorer._occluding_rows(*sl), sightlines)
        m_fast = median_ms(lambda *sl: blocker.occluding_rows(*sl),
                           sightlines, repeat=5)
        bench_rows.append(("occluding_rows", m_sql, m_fast))
        print(f"{'occluding_rows':<22} {m_sql:>9.2f} {m_fast:>9.3f} "
              f"{m_sql / m_fast if m_fast else float('inf'):>8.0f}x")

    m_sql = median_ms(
        lambda *p: _path_severance_penalty_m_sql(store_sql, *p), paths)
    m_fast = median_ms(lambda *p: severance.path_penalty_m(*p),
                       paths, repeat=5)
    bench_rows.append(("severance_path", m_sql, m_fast))
    print(f"{'severance_path':<22} {m_sql:>9.2f} {m_fast:>9.3f} "
          f"{m_sql / m_fast if m_fast else float('inf'):>8.0f}x")

    # ── simulated 100-cell crime scoring loop ────────────────────────
    cells = sample_coords(conn, 100)

    def crime_cell(store, lat, lon):
        """CrimeScorer per-block recipe: crime circle + kernel/decay
        accumulation + PLUTO unit-kernel denominator."""
        rows = store.query_circle(
            "crime", lat=lat, lon=lon, radius_m=400.0,
            select="law_cat_cd,cmplnt_fr_dt,latitude,longitude")
        decayed = 0.0
        for r in rows:
            d = float(r["_dist_m"])
            decayed += math.exp(-((d / 200.0) ** 2))
        units = 0.0
        for r in store.query_circle("pluto", lat=lat, lon=lon,
                                    radius_m=400.0, select="unitsres"):
            try:
                u = float(r.get("unitsres") or 0)
            except (TypeError, ValueError):
                continue
            units += u * math.exp(-((float(r["_dist_m"]) / 200.0) ** 2))
        return decayed, units

    crime_cell(store_fast, *cells[0])          # warm the index
    t0 = time.perf_counter()
    for lat, lon in cells:
        crime_cell(store_sql, lat, lon)
    t_sql = time.perf_counter() - t0
    t0 = time.perf_counter()
    for lat, lon in cells:
        crime_cell(store_fast, lat, lon)
    t_fast = time.perf_counter() - t0

    from apthunt import geohash as _gh
    rows = conn.execute(
        "SELECT lat, lon FROM listings "
        "WHERE UPPER(status)='ACTIVE' AND lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()
    n_blocks = len({_gh.encode(float(r["lat"]), float(r["lon"]))
                    for r in rows})
    print(f"\nSimulated crime scoring loop ({len(cells)} cells): "
          f"sqlite {t_sql:.2f}s vs fast {t_fast:.2f}s "
          f"→ {t_sql / t_fast:.0f}x")
    print(f"Projected over {n_blocks:,} active-listing blocks: "
          f"sqlite {t_sql / len(cells) * n_blocks:.0f}s vs "
          f"fast {t_fast / len(cells) * n_blocks:.1f}s (this dimension)")

    # ── keyed section (building dimensions) ──────────────────────────
    keyed_coords = sample_coords(conn, 400)
    keyed_section(conn, store_sql, keyed_coords, failures, args.bbls)

    finish(failures)
    conn.close()


if __name__ == "__main__":
    main()
