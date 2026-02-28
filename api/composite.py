"""
Composite score computation.

Calculates a weighted average of all 11 score dimensions.
Weights are configurable via priority ranking — the top 3
user priorities get a 3x multiplier, the middle tier gets 2x,
and the rest get 1x.
"""

from __future__ import annotations

# Default ordering when user has no custom priorities
DEFAULT_PRIORITIES: list[str] = [
    "transit",
    "deal",
    "crime",
    "noise",
    "amenity",
    "parks",
    "building_violations",
    "management",
    "schools",
    "flood_risk",
]

# Score column names in the DB → key used in Scores model
SCORE_KEYS: list[str] = [
    "deal",
    "transit",
    "flood_risk",
    "crime",
    "noise",
    "building_violations",
    "parks",
    "schools",
    "management",
    "amenity",
]


def compute_composite(
    scores: dict[str, float],
    priorities: list[str] | None = None,
) -> float:
    """
    Weighted composite score (0–100).

    Args:
        scores: mapping of dimension name → score (0–100).
        priorities: ordered list of dimension names, most important first.
                    Top 3 get weight 3, next 4 get weight 2, rest get weight 1.

    Returns:
        Weighted average, rounded to 1 decimal.
    """
    order = priorities or DEFAULT_PRIORITIES

    # Build weight map based on position
    weights: dict[str, int] = {}
    for i, key in enumerate(order):
        if i < 3:
            weights[key] = 3
        elif i < 7:
            weights[key] = 2
        else:
            weights[key] = 1

    # Any dimension not in the priority list gets weight 1
    for key in SCORE_KEYS:
        if key not in weights:
            weights[key] = 1

    total_weight = 0
    weighted_sum = 0.0
    for key in SCORE_KEYS:
        val = scores.get(key) or 0.0
        w = weights.get(key, 1)
        weighted_sum += val * w
        total_weight += w

    if total_weight == 0:
        return 0.0

    return round(weighted_sum / total_weight, 1)
