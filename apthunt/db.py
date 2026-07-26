"""
Database connection factory.

Centralizes the DB path and connection setup so that all modules
use the same configuration.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "apthunt.db",
)
DB_PATH = os.environ.get("APTHUNT_DB_PATH", DEFAULT_DB_PATH)


def inspect_database(db_path: str = DB_PATH) -> dict[str, Any]:
    """Return a lightweight health summary for the SQLite database."""
    summary: dict[str, Any] = {
        "path": db_path,
        "exists": os.path.isfile(db_path),
        "valid": False,
        "has_listings_table": False,
        "total_listings": 0,
        "api_visible_listings": 0,
        "error": None,
    }

    if not summary["exists"]:
        summary["error"] = "database file not found"
        return summary

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        quick_check = conn.execute("PRAGMA quick_check").fetchone()
        if not quick_check or quick_check[0] != "ok":
            summary["error"] = f"sqlite quick_check failed: {quick_check[0] if quick_check else 'unknown'}"
            return summary

        summary["valid"] = True

        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'listings'"
        ).fetchone()
        if not has_table:
            summary["error"] = "missing listings table"
            return summary

        summary["has_listings_table"] = True
        totals = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN UPPER(status) = 'ACTIVE' AND lat IS NOT NULL AND lon IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM listings"
        ).fetchone()
        summary["total_listings"] = int(totals[0] or 0)
        summary["api_visible_listings"] = int(totals[1] or 0)
        return summary
    except sqlite3.Error as exc:
        summary["error"] = str(exc)
        return summary
    finally:
        if conn is not None:
            conn.close()


def ensure_database_ready(
    db_path: str = DB_PATH,
    *,
    require_listings: bool = True,
) -> dict[str, Any]:
    """Validate the configured database and raise when it is not usable."""
    summary = inspect_database(db_path)
    if not summary["exists"]:
        raise RuntimeError(f"Database not found at {db_path}")
    if not summary["valid"]:
        detail = summary["error"] or "invalid SQLite database"
        raise RuntimeError(f"Database validation failed for {db_path}: {detail}")
    if not summary["has_listings_table"]:
        raise RuntimeError(f"Database at {db_path} is missing the listings table")
    if require_listings and summary["api_visible_listings"] <= 0:
        raise RuntimeError(
            f"Database at {db_path} has no API-visible ACTIVE listings with coordinates"
        )
    return summary


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Return a WAL-mode connection with Row factory.

    busy_timeout keeps concurrent writers (dataset downloads, baseline
    builds, scoring runs) waiting politely instead of dying with
    'database is locked'.
    """
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, much faster
    conn.row_factory = sqlite3.Row
    return conn
