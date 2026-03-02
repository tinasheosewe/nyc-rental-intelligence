"""
Composite score computation.

Groups 12 score dimensions into 5 categories, computes group averages,
then produces a weighted composite based on group priority ranking.
Top 2 groups get 3x, middle gets 2x, bottom 2 get 1x.
"""

from __future__ import annotations

# ── Score groups ────────────────────────────────────────────────

SCORE_GROUPS: dict[str, list[str]] = {
    "value": ["deal", "unit_amenities"],
    "access": ["transit"],
    "neighborhood": ["convenience", "parks", "greenery", "schools"],
    "safety": ["crime", "noise", "shelter"],
    "building": ["building_violations", "management", "pest"],
}

# Default group ordering when user has no custom priorities
DEFAULT_GROUP_PRIORITIES: list[str] = [
    "value",
    "safety",
    "building",
    "neighborhood",
    "access",
]

# All individual score column names in the DB
SCORE_KEYS: list[str] = [
    "deal",
    "unit_amenities",
    "transit",
    "crime",
    "noise",
    "building_violations",
    "parks",
    "schools",
    "management",
    "convenience",
    "shelter",
    "pest",
    "greenery",
]


def compute_group_scores(
    scores: dict[str, float | None],
    exclude_schools: bool = False,
) -> dict[str, float | None]:
    """Compute average score for each group from individual dimension scores.

    Dimensions with a None value (no data available) are excluded from the
    average so they don't drag down the group score.  If every dimension in
    a group is None, the group itself returns None (no data).
    """
    result: dict[str, float | None] = {}
    for group_key, dims in SCORE_GROUPS.items():
        effective = [d for d in dims if not (exclude_schools and d == "schools")]
        vals = [float(scores[d]) for d in effective if scores.get(d) is not None]
        result[group_key] = sum(vals) / len(vals) if vals else None
    return result


def compute_coverage(
    scores: dict[str, float | None],
    exclude_schools: bool = False,
) -> float:
    """Fraction of scored dimensions (0.0 – 1.0)."""
    dims = [k for k in SCORE_KEYS if not (exclude_schools and k == "schools")]
    scored = sum(1 for k in dims if scores.get(k) is not None)
    return scored / len(dims) if dims else 0.0


def data_quality_label(coverage: float) -> str | None:
    """Human-readable data-quality label.

    Returns:
        None        – ≥ 75 % of dimensions scored (trustworthy)
        "limited"   – 25-74 %
        "very_limited" – < 25 %
    """
    if coverage >= 0.75:
        return None
    if coverage >= 0.25:
        return "limited"
    return "very_limited"


def compute_composite(
    scores: dict[str, float],
    priorities: list[str] | None = None,
    exclude_schools: bool = False,
) -> tuple[float, str | None]:
    """
    Weighted composite score (0–100) with data-quality label.

    Computes group averages, then weights them by priority position:
        Top 2 groups → 3x weight
        Middle group → 2x weight
        Bottom 2 groups → 1x weight

    The score is NOT dampened — listings are ranked on available data
    as-is. A data_quality label is returned for UI disclosure only.

    Args:
        scores: mapping of dimension name → score (0–100).
        priorities: ordered list of group keys, most important first.
        exclude_schools: when True, omit schools from neighborhood avg.

    Returns:
        (composite, data_quality) where data_quality is one of
        None, "limited", or "very_limited".
    """
    group_scores = compute_group_scores(scores, exclude_schools=exclude_schools)
    coverage = compute_coverage(scores, exclude_schools=exclude_schools)
    order = priorities or DEFAULT_GROUP_PRIORITIES

    # Build weight map based on group position
    weights: dict[str, int] = {}
    for i, key in enumerate(order):
        if i < 2:
            weights[key] = 3
        elif i < 3:
            weights[key] = 2
        else:
            weights[key] = 1

    # Any group not in the priority list gets weight 1
    for key in SCORE_GROUPS:
        if key not in weights:
            weights[key] = 1

    total_weight = 0
    weighted_sum = 0.0
    for key in SCORE_GROUPS:
        val = group_scores.get(key)
        if val is None:
            continue  # skip groups with no scored dimensions
        w = weights.get(key, 1)
        weighted_sum += val * w
        total_weight += w

    if total_weight == 0:
        return (0.0, data_quality_label(0.0))

    composite = round(weighted_sum / total_weight, 1)
    return (composite, data_quality_label(coverage))
