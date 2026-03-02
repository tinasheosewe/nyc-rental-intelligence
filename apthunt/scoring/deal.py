"""
DealScorer — multi-signal value score.

Combines up to three signals when data is available:
  1. Price vs neighborhood median  (weight 0.40)
  2. $/sqft vs neighborhood median (weight 0.30)
  3. Absolute sqft vs neighborhood median (weight 0.30)

When sqft is unavailable the score falls back to price-only (weight 1.0).
Comp sets are grouped by (neighborhood, bed count) — no citywide fallback.
Scores are z-score normalized: 50 = mean, ±25 per stdev, clamped 0–100.
"""

from __future__ import annotations

import statistics
import sqlite3
from typing import Optional

from apthunt.scoring.base import Scorer, ScorerResult

MIN_COMP_SET = 3

# Signal weights (must sum to 1.0 for full-data case)
W_PRICE = 0.40
W_PRICE_PER_SQFT = 0.30
W_SQFT = 0.30


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
            "comp_sqft_median": "INTEGER",
            "price_per_sqft": "REAL",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        # Build comp sets from all active listings
        price_comps = self._build_price_comp_sets(conn)
        sqft_comps = self._build_sqft_comp_sets(conn)
        ppsqft_comps = self._build_ppsqft_comp_sets(conn)

        # Pass 1: compute weighted deviation + metadata per listing
        intermediate: list[tuple[str, float, dict]] = []
        for lst in listings:
            cost = _true_cost(lst)
            if cost is None:
                continue

            key = (lst["neighborhood"], lst["beds"])
            prices = price_comps.get(key, [])

            # Need at least MIN_COMP_SET price comps in neighborhood
            if len(prices) < MIN_COMP_SET:
                continue

            price_median = statistics.median(prices)
            set_size = len(prices)

            if price_median == 0:
                continue

            # Signal 1: price vs median (positive = cheaper = better)
            price_dev = (price_median - cost) / price_median

            # Check if we can use sqft signals
            listing_sqft = lst.get("sqft")
            sqft_values = sqft_comps.get(key, [])
            ppsqft_values = ppsqft_comps.get(key, [])
            has_sqft = (
                listing_sqft
                and listing_sqft > 0
                and len(sqft_values) >= MIN_COMP_SET
                and len(ppsqft_values) >= MIN_COMP_SET
            )

            meta: dict = {
                "comp_median": int(price_median),
                "comp_set_size": set_size,
                "comp_scope": "neighborhood",
                "comp_sqft_median": None,
                "price_per_sqft": None,
            }

            if has_sqft:
                sqft_median = statistics.median(sqft_values)
                ppsqft_median = statistics.median(ppsqft_values)
                listing_ppsqft = cost / listing_sqft

                meta["comp_sqft_median"] = int(sqft_median) if sqft_median else None
                meta["price_per_sqft"] = round(listing_ppsqft, 2)

                # Signal 2: $/sqft vs median (positive = cheaper per sqft = better)
                ppsqft_dev = (ppsqft_median - listing_ppsqft) / ppsqft_median if ppsqft_median else 0.0

                # Signal 3: absolute sqft vs median (positive = bigger = better)
                sqft_dev = (listing_sqft - sqft_median) / sqft_median if sqft_median else 0.0

                combined = (
                    W_PRICE * price_dev
                    + W_PRICE_PER_SQFT * ppsqft_dev
                    + W_SQFT * sqft_dev
                )
            else:
                # Graceful degradation: price-only
                combined = price_dev

            intermediate.append((lst["id"], combined, meta))

        # Pass 2: z-score normalization
        deviations = [r[1] for r in intermediate]
        scores = self._deviations_to_scores(deviations)

        results = []
        for i, (listing_id, _, meta) in enumerate(intermediate):
            results.append(ScorerResult(
                listing_id=listing_id,
                score=scores[i],
                components=meta,
            ))

        return results

    # ── Comp-set builders ───────────────────────────────────────

    def _build_price_comp_sets(self, conn: sqlite3.Connection) -> dict:
        rows = conn.execute(
            "SELECT neighborhood, beds, "
            "  CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END "
            "FROM listings WHERE price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
        ).fetchall()
        comp_sets: dict[tuple, list] = {}
        for neighborhood, beds, cost in rows:
            comp_sets.setdefault((neighborhood, beds), []).append(cost)
        return comp_sets

    def _build_sqft_comp_sets(self, conn: sqlite3.Connection) -> dict:
        """Absolute sqft grouped by (neighborhood, beds)."""
        rows = conn.execute(
            "SELECT neighborhood, beds, sqft "
            "FROM listings "
            "WHERE sqft IS NOT NULL AND sqft > 0 "
            "  AND price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
        ).fetchall()
        comp_sets: dict[tuple, list] = {}
        for neighborhood, beds, sqft in rows:
            comp_sets.setdefault((neighborhood, beds), []).append(sqft)
        return comp_sets

    def _build_ppsqft_comp_sets(self, conn: sqlite3.Connection) -> dict:
        """$/sqft grouped by (neighborhood, beds)."""
        rows = conn.execute(
            "SELECT neighborhood, beds, "
            "  CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END, "
            "  sqft "
            "FROM listings "
            "WHERE sqft IS NOT NULL AND sqft > 0 "
            "  AND price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
        ).fetchall()
        comp_sets: dict[tuple, list] = {}
        for neighborhood, beds, cost, sqft in rows:
            if sqft > 0:
                comp_sets.setdefault((neighborhood, beds), []).append(cost / sqft)
        return comp_sets

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
