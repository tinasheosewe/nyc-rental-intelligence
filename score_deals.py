"""
Deal Scorer
===========
Computes a deal score (0-100) for every listing in apthunt.db.

For each listing, compares its true cost against similar apartments
(same neighborhood + same bed count). Listings priced well below
their comp median score high; overpriced listings score low.

Usage:
    python3 score_deals.py
"""

import sqlite3
import os
import statistics

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apthunt.db")

MIN_COMP_SET = 3          # minimum listings to form a valid comp set

# ---------------------------------------------------------------------------
# Comp set computation
# ---------------------------------------------------------------------------

def build_comp_sets(conn: sqlite3.Connection) -> dict:
    """Build comp sets keyed by (neighborhood, beds).

    Returns:
        {
            ("East Village", 1): [3400, 4200, 4850, ...],
            ...
        }
    """
    rows = conn.execute(
        "SELECT neighborhood, beds, "
        "  CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END "
        "FROM listings WHERE price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
    ).fetchall()

    comp_sets = {}
    for neighborhood, beds, true_cost in rows:
        key = (neighborhood, beds)
        comp_sets.setdefault(key, []).append(true_cost)

    return comp_sets


def build_citywide_fallback(conn: sqlite3.Connection) -> dict:
    """Build fallback comp sets keyed by beds only.

    Returns:
        {0: [3250, 3500, ...], 1: [2200, 2995, ...], ...}
    """
    rows = conn.execute(
        "SELECT beds, "
        "  CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END "
        "FROM listings WHERE price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
    ).fetchall()

    fallback = {}
    for beds, true_cost in rows:
        fallback.setdefault(beds, []).append(true_cost)

    return fallback


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_deviation(true_cost: int, comp_median: float) -> float:
    """How far below the comp median this listing is, as a fraction.

    Positive = below median (good deal), negative = above (overpriced).
    """
    if comp_median == 0:
        return 0.0
    return (comp_median - true_cost) / comp_median


def deviations_to_scores(deviations: list[float]) -> list[float]:
    """Convert raw deviations to 0-100 scores using z-scores.

    50 = mean deviation (average deal)
    75 = 1 stdev above mean (good deal)
    100 = 2+ stdev above mean (exceptional deal)
    25 = 1 stdev below mean (overpriced)
    0 = 2+ stdev below mean (very overpriced)

    Scale is anchored to the actual data distribution,
    so scores hold consistent meaning across batches.
    """
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
        # Map z-score to 0-100: z=0 → 50, z=±2 → 100/0
        score = 50.0 + z * 25.0
        scores.append(round(max(0.0, min(100.0, score)), 1))

    return scores


def score_all(conn: sqlite3.Connection) -> dict:
    """Score every active listing. Returns stats."""
    comp_sets = build_comp_sets(conn)
    fallback = build_citywide_fallback(conn)

    listings = conn.execute(
        "SELECT id, neighborhood, beds, "
        "  CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END "
        "FROM listings WHERE price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
    ).fetchall()

    # Pass 1: compute deviation + metadata for every listing
    results = []
    neighborhood_scored = 0
    citywide_scored = 0

    for listing_id, neighborhood, beds, true_cost in listings:
        key = (neighborhood, beds)
        prices = comp_sets.get(key, [])

        if len(prices) >= MIN_COMP_SET:
            median = statistics.median(prices)
            scope = "neighborhood"
            set_size = len(prices)
            neighborhood_scored += 1
        else:
            prices = fallback.get(beds, [])
            median = statistics.median(prices)
            scope = "citywide"
            set_size = len(prices)
            citywide_scored += 1

        deviation = compute_deviation(true_cost, median)
        results.append((listing_id, deviation, int(median), set_size, scope))

    # Pass 2: convert deviations to percentile scores
    deviations = [r[1] for r in results]
    scores = deviations_to_scores(deviations)

    for i, (listing_id, _, median, set_size, scope) in enumerate(results):
        conn.execute(
            "UPDATE listings SET deal_score=?, comp_median=?, comp_set_size=?, comp_scope=? WHERE id=?",
            (scores[i], median, set_size, scope, listing_id)
        )

    conn.commit()

    return {
        "scored": len(results),
        "neighborhood_comps": neighborhood_scored,
        "citywide_fallback": citywide_scored,
    }


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_results(conn: sqlite3.Connection, stats: dict):
    print(f"\n{'='*70}")
    print(f"  DEAL SCORING RESULTS")
    print(f"{'='*70}")
    print(f"  Listings scored:        {stats['scored']}")
    print(f"  Neighborhood comp sets: {stats['neighborhood_comps']}")
    print(f"  Citywide fallback:      {stats['citywide_fallback']}")

    # Top 10 deals
    top = conn.execute("""
        SELECT deal_score,
               CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END as true_cost,
               comp_median, comp_scope, comp_set_size,
               neighborhood, beds, address, unit, url
        FROM listings
        WHERE deal_score IS NOT NULL
        ORDER BY deal_score DESC
        LIMIT 10
    """).fetchall()

    print(f"\n  TOP 10 DEALS")
    print(f"  {'Score':>5}  {'TrueCost':>8}  {'Median':>7}  {'Comp':>5}  {'Set':>3}  {'Neighborhood':<22}  {'Beds':>4}  Address")
    print(f"  {'-'*5}  {'-'*8}  {'-'*7}  {'-'*5}  {'-'*3}  {'-'*22}  {'-'*4}  {'-'*20}")

    for row in top:
        score, true_cost, median, scope, set_size, hood, beds, addr, unit, url = row
        scope_tag = "nbhd" if scope == "neighborhood" else "city"
        unit_str = f" #{unit}" if unit else ""
        print(f"  {score:5.1f}  ${true_cost:>7,}  ${median:>6,}  {scope_tag:>5}  {set_size:>3}  {hood:<22}  {beds:>4}  {addr}{unit_str}")

    # Bottom 5
    bottom = conn.execute("""
        SELECT deal_score,
               CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END as true_cost,
               comp_median, comp_scope, comp_set_size,
               neighborhood, beds, address, unit
        FROM listings
        WHERE deal_score IS NOT NULL
        ORDER BY deal_score ASC
        LIMIT 5
    """).fetchall()

    print(f"\n  BOTTOM 5 (most overpriced)")
    print(f"  {'Score':>5}  {'TrueCost':>8}  {'Median':>7}  {'Comp':>5}  {'Set':>3}  {'Neighborhood':<22}  {'Beds':>4}  Address")
    print(f"  {'-'*5}  {'-'*8}  {'-'*7}  {'-'*5}  {'-'*3}  {'-'*22}  {'-'*4}  {'-'*20}")

    for row in bottom:
        score, true_cost, median, scope, set_size, hood, beds, addr, unit = row
        scope_tag = "nbhd" if scope == "neighborhood" else "city"
        unit_str = f" #{unit}" if unit else ""
        print(f"  {score:5.1f}  ${true_cost:>7,}  ${median:>6,}  {scope_tag:>5}  {set_size:>3}  {hood:<22}  {beds:>4}  {addr}{unit_str}")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    conn = sqlite3.connect(DB_PATH)
    stats = score_all(conn)
    print_results(conn, stats)
    conn.close()


if __name__ == "__main__":
    main()
