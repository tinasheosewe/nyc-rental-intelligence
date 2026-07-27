"""
Composite score computation.

Groups 12 score dimensions into 5 categories, computes group averages,
then produces a weighted composite based on group priority ranking.
Top 2 groups get 3x, middle gets 2x, bottom 2 get 1x.
"""

from __future__ import annotations

# ── Score groups ────────────────────────────────────────────────

# Group membership principle: "building" holds ONLY building-verified
# signals (a clean building must not be dragged down by its block);
# "safety" is actual physical safety (crime, traffic danger); area
# livability signals (noise, air) live in "neighborhood".
SCORE_GROUPS: dict[str, list[str]] = {
    "value": ["deal", "unit_amenities"],
    "access": ["transit"],
    "neighborhood": ["convenience", "parks", "greenery", "schools",
                     "air_quality", "noise", "road_exposure"],
    "safety": ["crime", "street_danger", "shelter"],
    "building": ["building_violations", "management", "pest", "bedbug"],
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
    "bedbug",
    "street_danger",
    "air_quality",
    "road_exposure",
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


# Dimensions a renter would genuinely veto on — a bottom-decile score on
# one of these caps the composite rather than averaging away. Deliberately
# excludes dimensions that are structurally low across whole swaths of the
# city (air quality, noise, shelter percentiles in the dense core).
DEALBREAKER_DIMS: frozenset = frozenset(
    {"bedbug", "building_violations", "management", "pest", "crime"}
)
DEALBREAKER_THRESHOLD = 12.0
DEALBREAKER_CAP = 55.0


def compute_composite(
    scores: dict[str, float],
    priorities: list[str] | None = None,
    exclude_schools: bool = False,
    boosts: list[str] | None = None,
    ignore: list[str] | None = None,
) -> tuple[float, str | None]:
    """
    Weighted RAW composite (0–100) with data-quality label.

    Weighting model: every group matters equally by default (they are all
    important — forced rank ordering was a fake question). Callers may
    ``boost`` up to two groups (×2 weight) and ``ignore`` individual
    dimensions entirely (excluded from their group's average).

    Dealbreaker rule: any DEALBREAKER_DIMS dimension scoring at or below
    DEALBREAKER_THRESHOLD caps the composite at DEALBREAKER_CAP — a
    tenement with an active bedbug problem must not average its way to 90.

    Legacy compat: ``priorities`` (the old ranked list) maps its top two
    entries to boosts.

    NOTE: this raw value compresses toward the middle (it is a mean of
    many percentiles). Display/ranking paths should percentile-ize it via
    ``composite_percentile()`` so the top listing in the city reads ~100,
    not ~80.

    Returns:
        (raw_composite, data_quality)
    """
    ignore_set = set(ignore or [])
    if exclude_schools:
        ignore_set.add("schools")

    # Group averages with ignored dimensions excluded
    group_scores: dict[str, float | None] = {}
    for group_key, dims in SCORE_GROUPS.items():
        vals = [
            float(scores[d]) for d in dims
            if d not in ignore_set and scores.get(d) is not None
        ]
        group_scores[group_key] = sum(vals) / len(vals) if vals else None

    coverage = compute_coverage(scores, exclude_schools=exclude_schools)

    # Weights: 1.0 baseline, boosted groups ×2 (max two boosts)
    boost_keys = list(boosts or [])
    if not boost_keys and priorities:
        boost_keys = [k for k in priorities[:2] if k in SCORE_GROUPS]
    weights = {key: (2.0 if key in boost_keys[:2] else 1.0) for key in SCORE_GROUPS}

    total_weight = 0.0
    weighted_sum = 0.0
    for key, val in group_scores.items():
        if val is None:
            continue
        weighted_sum += val * weights[key]
        total_weight += weights[key]

    if total_weight == 0:
        return (0.0, data_quality_label(0.0))

    composite = weighted_sum / total_weight

    # NB: the dealbreaker cap is NOT applied here. Capping the RAW
    # composite before percentile-izing inverted the intent: raw 55 sat at
    # the 67th percentile of raw composites, so capped listings displayed
    # 67.2 — above the cap — and 11% of listings clumped there. Callers
    # percentile-ize first, then apply apply_dealbreaker_cap() on the
    # percentile scale.
    return (round(composite, 1), data_quality_label(coverage))


def apply_dealbreaker_cap(
    display_composite: float,
    scores: dict[str, float],
    ignore: list[str] | None = None,
) -> float:
    """Cap the DISPLAYED (percentile) composite when a dealbreaker dim is
    in the gutter. Applied after percentile-izing — never before."""
    ignore_set = set(ignore or [])
    for dim in DEALBREAKER_DIMS:
        if dim in ignore_set:
            continue
        val = scores.get(dim)
        if val is not None and float(val) <= DEALBREAKER_THRESHOLD:
            return round(min(display_composite, DEALBREAKER_CAP), 1)
    return display_composite


def dealbreakers(scores: dict[str, float]) -> list[str]:
    """Dimensions currently triggering the dealbreaker cap."""
    return sorted(
        dim for dim in DEALBREAKER_DIMS
        if scores.get(dim) is not None
        and float(scores[dim]) <= DEALBREAKER_THRESHOLD
    )


def composite_percentile(conn, raw_composite: float) -> float:
    """Map a raw composite onto its percentile among active listings.

    The raw composite is a mean of many percentiles and therefore
    concentrates in a narrow band (citywide max ~80) — every listing
    reads "Good". Percentile-izing restores the full 0-100 range with an
    honest meaning: 92 = better overall than 92% of active NYC listings.

    Uses the frozen distribution stored by scripts/compute_composites.py
    under baseline_dist dimension "__composite__"; falls back to the raw
    value when no distribution exists yet.
    """
    from apthunt.scoring.baseline import baseline_scores

    result = baseline_scores(conn, "__composite__", [raw_composite])
    if result is None or result[0] is None:
        return raw_composite
    return result[0]
