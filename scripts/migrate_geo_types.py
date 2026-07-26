#!/usr/bin/env python3
"""
One-time migration: rebuild legacy ds_* tables so geo/numeric columns are
REAL-typed instead of TEXT.

Why: query_bbox no longer wraps geo columns in CAST (which defeated every
index), so the columns themselves must carry numeric affinity. TEXT-affinity
comparison against numbers falls back to string ordering, which is wrong for
negative longitudes ('-73.9' < '-74.0' lexicographically).

Usage:
    python3 scripts/migrate_geo_types.py            # migrate all stale tables
    python3 scripts/migrate_geo_types.py --check    # report only
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt.db import get_connection, DB_PATH
from apthunt.data.data_store import DATASETS


def column_types(conn, table: str) -> dict:
    return {row[1]: (row[2] or "").upper() for row in conn.execute(f"PRAGMA table_info([{table}])")}


def migrate_table(conn, table: str, numeric_cols: set) -> bool:
    cols = column_types(conn, table)
    if not cols:
        return False  # table doesn't exist
    needs = [c for c in numeric_cols if c in cols and cols[c] != "REAL"]
    if not needs:
        return False

    col_defs = ", ".join(
        f"[{c}] REAL" if c in numeric_cols else f"[{c}] {cols[c] or 'TEXT'}"
        for c in cols
    )
    select_exprs = ", ".join(
        f"CAST(NULLIF([{c}], '') AS REAL)" if c in needs else f"[{c}]"
        for c in cols
    )

    # Capture existing index definitions to recreate after the rebuild
    idx_sql = [
        row[0] for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        )
    ]

    t0 = time.time()
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(f"CREATE TABLE [{table}__mig] ({col_defs})")
    conn.execute(f"INSERT INTO [{table}__mig] SELECT {select_exprs} FROM [{table}]")
    conn.execute(f"DROP TABLE [{table}]")
    conn.execute(f"ALTER TABLE [{table}__mig] RENAME TO [{table}]")
    for sql in idx_sql:
        conn.execute(sql)
    conn.execute("COMMIT")
    print(f"  {table}: retyped {needs} in {time.time() - t0:.1f}s")
    return True


def main():
    check_only = "--check" in sys.argv
    conn = get_connection(DB_PATH)
    migrated = 0
    for name, ddef in DATASETS.items():
        table = f"ds_{name}"
        numeric = set(ddef.geo_columns) | set(ddef.real_columns)
        if not numeric:
            continue
        cols = column_types(conn, table)
        if not cols:
            continue
        stale = [c for c in numeric if c in cols and cols[c] != "REAL"]
        if check_only:
            if stale:
                print(f"  {table}: TEXT-typed geo columns {stale}")
            continue
        if migrate_table(conn, table, numeric):
            migrated += 1
    # No VACUUM: dropped pages are reused by subsequent downloads, and
    # vacuuming a multi-GB DB would block for minutes for no functional gain.
    conn.close()
    print(f"Done. {migrated} tables migrated." if not check_only else "Check complete.")


if __name__ == "__main__":
    main()
