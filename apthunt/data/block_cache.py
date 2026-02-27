"""
Block-level data cache backed by SQLite.

Schema:
    block_cache (
        geohash     TEXT,
        source      TEXT,       -- 'crime', '311', 'pluto', 'transit'
        fetched_at  TEXT,
        data        TEXT,       -- JSON blob
        PRIMARY KEY (geohash, source)
    )

TTL: 7 days (configurable). Stale entries are re-fetched on next access.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional

DEFAULT_TTL_DAYS = 7


class BlockCache:
    """Read/write interface for the block_cache table."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        ttl_days: int = DEFAULT_TTL_DAYS,
    ):
        self._conn = conn
        self._ttl = timedelta(days=ttl_days)
        self._ensure_table()

    def _ensure_table(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS block_cache (
                geohash     TEXT NOT NULL,
                source      TEXT NOT NULL,
                fetched_at  TEXT NOT NULL,
                data        TEXT NOT NULL,
                PRIMARY KEY (geohash, source)
            )
        """)
        self._conn.commit()

    def get(self, geohash: str, source: str) -> Optional[Any]:
        """Return cached data if fresh, else None."""
        row = self._conn.execute(
            "SELECT data, fetched_at FROM block_cache "
            "WHERE geohash=? AND source=?",
            (geohash, source),
        ).fetchone()

        if row is None:
            return None

        data, fetched_at = row[0], row[1]
        fetched = datetime.fromisoformat(fetched_at)
        if datetime.now(timezone.utc) - fetched > self._ttl:
            return None  # stale

        return json.loads(data)

    def put(self, geohash: str, source: str, data: Any):
        """Write data to cache (upsert)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO block_cache "
            "(geohash, source, fetched_at, data) VALUES (?, ?, ?, ?)",
            (geohash, source, now, json.dumps(data)),
        )

    def get_or_fetch(
        self,
        geohash: str,
        source: str,
        fetch_fn: Callable[[], Any],
    ) -> Any:
        """Return cached data or call fetch_fn() and cache the result."""
        cached = self.get(geohash, source)
        if cached is not None:
            return cached
        data = fetch_fn()
        self.put(geohash, source, data)
        return data

    def stale_geohashes(
        self,
        source: str,
        geohashes: list[str],
    ) -> list[str]:
        """Return geohashes from the input list that are missing or stale."""
        if not geohashes:
            return []

        cutoff = (
            datetime.now(timezone.utc) - self._ttl
        ).isoformat()

        placeholders = ",".join("?" for _ in geohashes)
        fresh = {
            row[0]
            for row in self._conn.execute(
                f"SELECT geohash FROM block_cache "
                f"WHERE source=? AND fetched_at > ? "
                f"AND geohash IN ({placeholders})",
                [source, cutoff] + geohashes,
            ).fetchall()
        }

        return [g for g in geohashes if g not in fresh]

    def purge_stale(self, source: Optional[str] = None) -> int:
        """Delete stale cache entries. Returns count deleted."""
        cutoff = (
            datetime.now(timezone.utc) - self._ttl
        ).isoformat()

        if source:
            cur = self._conn.execute(
                "DELETE FROM block_cache WHERE source=? AND fetched_at < ?",
                (source, cutoff),
            )
        else:
            cur = self._conn.execute(
                "DELETE FROM block_cache WHERE fetched_at < ?",
                (cutoff,),
            )
        self._conn.commit()
        return cur.rowcount
