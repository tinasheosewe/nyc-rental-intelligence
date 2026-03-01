"""Shared scoring utilities used across all percentile-ranked scorers.

All scorers that convert raw per-listing values into 0–100 percentile scores
should use the functions here so tie-handling, clamping, and edge-case logic
stay consistent in one place.
"""

from __future__ import annotations


def percentile_scores(
    values: list[float],
    *,
    reverse: bool = False,
    zero_is_perfect: bool = False,
) -> list[float]:
    """Convert raw values to 0–100 percentile scores with proper tie handling.

    Tied values receive the **same** percentile (average of their ranks).

    Args:
        values:  One raw value per listing, in listing order.
        reverse: If ``True``, *lower* raw values get *higher* scores
                 (appropriate when fewer = better, e.g. complaints).
        zero_is_perfect:
            If ``True`` **and** ``reverse=True``, any entry whose raw value
            is exactly ``0`` is pinned to ``100.0`` regardless of its
            percentile position.  Use this for "no complaints / no
            violations = perfect" semantics.

    Returns:
        A list of floats (same length as *values*), each in [0, 100].
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [100.0 if (zero_is_perfect and reverse and values[0] == 0) else 50.0]

    # Sort by value, tracking original indices
    indexed = sorted(enumerate(values), key=lambda t: t[1])

    # Assign average rank to tied groups (0-based ranks)
    scores = [0.0] * n
    i = 0
    while i < n:
        # Find the run of identical values
        j = i
        while j < n and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j - 1) / 2.0
        avg_pct = avg_rank / (n - 1) * 100.0
        for k in range(i, j):
            idx = indexed[k][0]
            scores[idx] = (100.0 - avg_pct) if reverse else avg_pct
        i = j

    # Zero-clamp: 0 raw value → perfect score when appropriate
    if zero_is_perfect and reverse:
        for idx, val in enumerate(values):
            if val == 0:
                scores[idx] = 100.0

    return scores
