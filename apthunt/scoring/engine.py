"""
ScoringEngine — orchestrates running all registered scorers.

Responsibilities:
1. Ensure DB schema has columns for all registered scorers
2. Load active listings with lat/lon
3. Compute geohashes
4. Run each scorer
5. Write results back to listings table
"""

from __future__ import annotations

from typing import Any
import sqlite3

from apthunt import geohash
from apthunt.scoring.base import Scorer, ScorerResult


class ScoringEngine:

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._scorers: list[Scorer] = []

    def register(self, scorer: Scorer) -> "ScoringEngine":
        """Register a scorer. Returns self for chaining."""
        self._scorers.append(scorer)
        return self

    def run(self, listing_ids: list[str] | None = None) -> dict[str, Any]:
        """Run all registered scorers against active listings.

        Args:
            listing_ids: if provided, score only these. Otherwise all active.

        Returns:
            Stats dict with scoring summary.
        """
        conn = self._conn

        self._ensure_columns(conn)
        listings = self._load_listings(conn, listing_ids)

        for lst in listings:
            lst["geohash"] = geohash.encode(lst["lat"], lst["lon"])

        stats: dict[str, Any] = {}
        for scorer in self._scorers:
            results = scorer.score(conn, listings)
            self._write_results(conn, scorer, results)
            stats[scorer.name] = {"scored": len(results)}

        conn.commit()
        return {"total_scored": len(listings), "scorers": stats}

    def _ensure_columns(self, conn: sqlite3.Connection):
        """Add any missing columns for registered scorers."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
        for scorer in self._scorers:
            score_col = f"{scorer.name}_score"
            if score_col not in existing:
                conn.execute(f"ALTER TABLE listings ADD COLUMN {score_col} REAL")
                existing.add(score_col)
            for col_name, col_type in scorer.columns().items():
                if col_name not in existing:
                    conn.execute(f"ALTER TABLE listings ADD COLUMN {col_name} {col_type}")
                    existing.add(col_name)
        conn.commit()

    def _load_listings(
        self, conn: sqlite3.Connection, listing_ids: list[str] | None
    ) -> list[dict]:
        """Load active listings as list of dicts."""
        if listing_ids:
            placeholders = ",".join("?" for _ in listing_ids)
            rows = conn.execute(
                f"SELECT * FROM listings WHERE id IN ({placeholders}) "
                "AND lat IS NOT NULL AND lon IS NOT NULL",
                listing_ids,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM listings "
                "WHERE UPPER(status) = 'ACTIVE' "
                "AND lat IS NOT NULL AND lon IS NOT NULL"
            ).fetchall()

        return [dict(row) for row in rows]

    def _write_results(
        self,
        conn: sqlite3.Connection,
        scorer: Scorer,
        results: list[ScorerResult],
    ):
        """Write ScorerResult scores and components to the listings table."""
        score_col = f"{scorer.name}_score"
        extra_cols = scorer.columns()

        for result in results:
            sets = [f"{score_col}=?"]
            vals: list[Any] = [result.score]
            for col_name in extra_cols:
                if col_name in result.components:
                    sets.append(f"{col_name}=?")
                    vals.append(result.components[col_name])
            vals.append(result.listing_id)
            conn.execute(
                f"UPDATE listings SET {', '.join(sets)} WHERE id=?",
                vals,
            )
