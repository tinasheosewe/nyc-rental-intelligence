"""
DealScorer — compares each listing's true cost against its comp set median.

Comp sets are grouped by (neighborhood, bed count).
Falls back to citywide by bed count if the neighborhood set is too small.
Scores are z-score normalized: 50 = mean, ±25 per stdev, clamped 0–100.
"""

from __future__ import annotations

import statistics
import sqlite3

from apthunt.scoring.base import Scorer, ScorerResult

MIN_COMP_SET = 3


def _true_cost(listing: dict) -> int | None:
    """Net effective price if available, otherwise ask price."""
    net = listing.get("net_effective_price")
    if net and net > 0:
        return net
    return listing.get("price")


class DealScorer(Scorer):

    @property
    def name(self) -> str:
        return "deal"

    def columns(self) -> dict[str, str]:
        return {
            "comp_median": "INTEGER",
            "comp_set_size": "INTEGER",
            "comp_scope": "TEXT",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        # Build comp sets from all active listings (not just the batch)
        comp_sets = self._build_comp_sets(conn)
        fallback = self._build_citywide_fallback(conn)

        # Pass 1: compute deviation + metadata
        intermediate: list[tuple[str, float, int, int, str]] = []
        for lst in listings:
            cost = _true_cost(lst)
            if cost is None:
                continue

            key = (lst["neighborhood"], lst["beds"])
            prices = comp_sets.get(key, [])

            if len(prices) >= MIN_COMP_SET:
                median = statistics.median(prices)
                scope = "neighborhood"
                set_size = len(prices)
            else:
                prices = fallback.get(lst["beds"], [])
                median = statistics.median(prices)
                scope = "citywide"
                set_size = len(prices)

            if median == 0:
                deviation = 0.0
            else:
                deviation = (median - cost) / median

            intermediate.append((lst["id"], deviation, int(median), set_size, scope))

        # Pass 2: z-score normalization
        deviations = [r[1] for r in intermediate]
        scores = self._deviations_to_scores(deviations)

        results = []
        for i, (listing_id, _, median, set_size, scope) in enumerate(intermediate):
            results.append(ScorerResult(
                listing_id=listing_id,
                score=scores[i],
                components={
                    "comp_median": median,
                    "comp_set_size": set_size,
                    "comp_scope": scope,
                },
            ))

        return results

    def _build_comp_sets(self, conn: sqlite3.Connection) -> dict:
        rows = conn.execute(
            "SELECT neighborhood, beds, "
            "  CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END "
            "FROM listings WHERE price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
        ).fetchall()
        comp_sets: dict[tuple, list] = {}
        for neighborhood, beds, cost in rows:
            comp_sets.setdefault((neighborhood, beds), []).append(cost)
        return comp_sets

    def _build_citywide_fallback(self, conn: sqlite3.Connection) -> dict:
        rows = conn.execute(
            "SELECT beds, "
            "  CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END "
            "FROM listings WHERE price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
        ).fetchall()
        fallback: dict[int, list] = {}
        for beds, cost in rows:
            fallback.setdefault(beds, []).append(cost)
        return fallback

    @staticmethod
    def _deviations_to_scores(deviations: list[float]) -> list[float]:
        """Z-score normalization: 50 = mean, ±25 per stdev, clamped 0–100."""
        if not deviations:
            return []
        if len(deviations) == 1:
            return [50.0]

        mean = statistics.mean(deviations)
        stdev = statistics.stdev(deviations)

        if stdev == 0:
            return [50.0] * len(deviations)

        scores = []
        for dev in deviations:
            z = (dev - mean) / stdev
            score = 50.0 + z * 25.0
            scores.append(round(max(0.0, min(100.0, score)), 1))
        return scores
