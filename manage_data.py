#!/usr/bin/env python3
"""
Manage local dataset downloads.

Usage:
    python3 manage_data.py                     # download all missing datasets
    python3 manage_data.py --only parks,pluto  # download specific datasets
    python3 manage_data.py --force             # re-download everything
    python3 manage_data.py --status            # show dataset freshness
    python3 manage_data.py --refresh           # prod: refresh stale datasets
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from apthunt.db import get_connection, DB_PATH
from apthunt.data.data_store import DataStore, DATASETS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Download and manage local scoring datasets"
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated dataset names (e.g. parks,pluto,crime)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if data is fresh",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show dataset freshness and exit",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh all stale datasets (respects cadence in prod)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DB_PATH,
        help="Path to the SQLite database",
    )
    args = parser.parse_args()

    conn = get_connection(args.db)
    store = DataStore(
        conn,
        app_token=os.environ.get("SODA_APP_TOKEN"),
    )

    if args.status:
        _print_status(store)
        conn.close()
        return

    if args.refresh:
        log.info("Refreshing stale datasets ...")
        results = store.refresh_stale(force=args.force)
        _print_results(results)
        conn.close()
        return

    # Determine which datasets to download
    if args.only:
        names = [n.strip() for n in args.only.split(",")]
        unknown = set(names) - set(DATASETS)
        if unknown:
            print(f"Unknown datasets: {', '.join(unknown)}", file=sys.stderr)
            print(f"Available: {', '.join(sorted(DATASETS))}", file=sys.stderr)
            sys.exit(1)
    else:
        names = list(DATASETS.keys())

    log.info("Downloading %d dataset(s): %s", len(names), ", ".join(names))
    t0 = time.time()

    results = {}
    failures = {}
    for name in names:
        try:
            results[name] = store.download(name, force=args.force)
        except Exception as exc:  # one flaky dataset must not kill the batch
            log.error("%s: download FAILED: %s", name, exc)
            failures[name] = str(exc)
            results[name] = {"downloaded": False, "rows": 0, "elapsed_sec": 0}

    elapsed = time.time() - t0
    _print_results(results)
    log.info("Total time: %.1fs", elapsed)
    conn.close()
    if failures:
        print(f"\n{len(failures)} dataset(s) FAILED: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


def _print_status(store: DataStore):
    """Print a table of dataset statuses."""
    statuses = store.status()
    print(f"\n{'Dataset':<20} {'Rows':>10} {'Refreshed':>22} {'Cadence':>10} {'Status':>8}")
    print("-" * 74)
    for s in statuses:
        rows = f"{s['row_count']:,}" if s["row_count"] else "—"
        refreshed = s["refreshed_at"][:19] if s.get("refreshed_at") else "never"
        cadence = f"{s.get('refresh_days', '?')}d"
        status = "STALE" if s.get("stale") else "fresh"
        print(f"{s['dataset']:<20} {rows:>10} {refreshed:>22} {cadence:>10} {status:>8}")
    print()


def _print_results(results: dict):
    """Print download results."""
    for name, info in results.items():
        if info["downloaded"]:
            print(f"  ✓ {name}: {info['rows']:,} rows in {info['elapsed_sec']:.1f}s")
        else:
            print(f"  — {name}: skipped (already fresh)")


if __name__ == "__main__":
    main()
