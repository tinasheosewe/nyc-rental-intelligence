"""
Neighborhood peer context.

Scores stay citywide (absolute) — this module ADDS a per-dimension
annotation ranking a listing against its actual alternatives: other
ACTIVE listings in the same neighborhood. For each scored dimension we
report the listing's percentile among neighborhood peers, the peer
count, and the neighborhood median score.

Per-neighborhood sorted score lists are cached in-process for 5 minutes
so repeated detail views don't re-query the peer set.
"""

from __future__ import annotations

import time
from bisect import bisect_left, bisect_right
from statistics import median

from api.composite import SCORE_KEYS

# Dimensions with fewer than this many scored peers are skipped —
# a percentile among a handful of listings is noise, not context.
MIN_PEERS = 20

_CACHE_TTL_SECONDS = 300

# neighborhood → (fetched_at, {dim: sorted list of peer scores})
_cache: dict[str, tuple[float, dict[str, list[float]]]] = {}


def _get_peer_scores(conn, neighborhood: str) -> dict[str, list[float]]:
    """Sorted per-dimension score lists for a neighborhood's active listings."""
    now = time.time()
    cached = _cache.get(neighborhood)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    score_cols = ", ".join(f"{k}_score" for k in SCORE_KEYS)
    rows = conn.execute(
        f"SELECT {score_cols} FROM listings "
        "WHERE neighborhood = ? AND UPPER(status) = 'ACTIVE'",
        (neighborhood,),
    ).fetchall()

    by_dim: dict[str, list[float]] = {}
    for key in SCORE_KEYS:
        col = f"{key}_score"
        by_dim[key] = sorted(
            float(row[col]) for row in rows if row[col] is not None
        )

    _cache[neighborhood] = (now, by_dim)
    return by_dim


def get_peer_context(conn, listing_row: dict) -> dict[str, dict[str, float]]:
    """Per-dimension neighborhood ranking for a listing.

    Returns {dim: {"nbhd_percentile": 0-100, "n_peers": int,
    "nbhd_median": float}} for each dimension where the listing has a
    score and the neighborhood has at least MIN_PEERS scored peers.
    """
    neighborhood = listing_row.get("neighborhood")
    if not neighborhood:
        return {}

    by_dim = _get_peer_scores(conn, neighborhood)

    out: dict[str, dict[str, float]] = {}
    for key in SCORE_KEYS:
        raw = listing_row.get(f"{key}_score")
        if raw is None:
            continue
        peers = by_dim.get(key) or []
        n = len(peers)
        if n < MIN_PEERS:
            continue
        score = float(raw)
        # Midrank percentile: strictly-below peers plus half the ties.
        lo = bisect_left(peers, score)
        hi = bisect_right(peers, score)
        percentile = 100.0 * (lo + (hi - lo) / 2.0) / n
        out[key] = {
            "nbhd_percentile": int(round(percentile)),
            "n_peers": n,
            "nbhd_median": round(median(peers), 1),
        }
    return out
