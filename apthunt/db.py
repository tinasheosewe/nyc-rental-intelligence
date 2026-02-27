"""
Database connection factory.

Centralizes the DB path and connection setup so that all modules
use the same configuration.
"""

from __future__ import annotations

import os
import sqlite3

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "apthunt.db",
)


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Return a WAL-mode connection with Row factory."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn
