#!/usr/bin/env python3
"""
Load listings from a listing source into the canonical database.

Usage:
    python3 ingest.py                        # the bundled synthetic sample (300 listings)
    python3 ingest.py --opt count=1000       # ...more of them
    python3 ingest.py --opt seed=7 --opt as_of=2026-07-01
    python3 ingest.py --source <name>        # any registered source
    python3 ingest.py --list                 # show registered sources
    python3 ingest.py --reset                # drop existing listings first

Sources are pluggable — see apthunt/ingest/__init__.py. The only one that
ships with this repository is `sample`, which generates fictional listings.
Run the scorers afterwards:

    python3 run_scores.py && python3 scripts/compute_composites.py
"""

from __future__ import annotations

import argparse
import sys

from apthunt.db import DB_PATH, get_connection
from apthunt.ingest import available_sources, get_source, init_schema, sync


def _parse_options(pairs: list[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise SystemExit(f"--opt expects KEY=VALUE, got {pair!r}")
        options[key.replace("-", "_")] = value
    return options


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest listings into the canonical DB")
    parser.add_argument("--source", default="sample", help="registered source name (default: sample)")
    parser.add_argument("--opt", action="append", default=[], metavar="KEY=VALUE",
                        help="option passed to the source (repeatable), e.g. count=500")
    parser.add_argument("--db", default=DB_PATH, help="path to the SQLite database")
    parser.add_argument("--reset", action="store_true",
                        help="delete all existing listings before ingesting")
    parser.add_argument("--list", action="store_true", help="list registered sources and exit")
    args = parser.parse_args()

    if args.list:
        print("Registered listing sources:")
        for name in available_sources():
            print(f"  {name}")
        return

    try:
        source = get_source(args.source, **_parse_options(args.opt))
    except (TypeError, ValueError) as exc:
        print(f"Cannot set up source {args.source!r}: {exc}", file=sys.stderr)
        sys.exit(1)

    conn = get_connection(args.db)
    try:
        init_schema(conn)
        if args.reset:
            removed = conn.execute("DELETE FROM listings").rowcount
            conn.commit()
            print(f"Removed {removed} existing listings.")
        stats = sync(conn, source)
        active = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE UPPER(status) = 'ACTIVE'"
        ).fetchone()[0]
    finally:
        conn.close()

    print(
        f"{source.name}: {stats['seen']} listings "
        f"({stats['inserted']} new, {stats['updated']} refreshed, "
        f"{stats['deactivated']} marked inactive)"
    )
    print(f"Active listings in {args.db}: {active}")


if __name__ == "__main__":
    main()
