"""
Citywide baseline distributions — absolute scoring for every dimension.

Problem this solves
-------------------
Scorers previously ranked listings against *whatever batch was being
scored* (``percentile_scores``) or against the median of currently-cached
blocks (``median_inverse_scores``).  Both are relative measures: a single
pasted listing gets a meaningless ~50, scores drift as inventory changes,
and distributions compress toward the middle.

The fix: score every raw block/building metric against a **frozen citywide
distribution** sampled over residential NYC.  ``scripts/build_baseline.py``
computes each dimension's raw metric across a large sample of residential
grid cells and stores a 1001-point quantile grid here.  At scoring time a
raw value maps to its citywide percentile via interpolation — absolute,
reproducible, and defined for a single listing.

Table
-----
``baseline_dist(dimension PK, updated_at, n, reverse, zero_is_perfect,
quantiles JSON)`` — quantiles is a sorted list of 1001 values
(q0.000, q0.001, ..., q1.000) of the raw metric.
"""

from __future__ import annotations

import bisect
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Sequence

log = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS baseline_dist (
    dimension       TEXT PRIMARY KEY,
    updated_at      TEXT NOT NULL,
    n               INTEGER NOT NULL,
    reverse         INTEGER NOT NULL DEFAULT 0,
    zero_is_perfect INTEGER NOT NULL DEFAULT 0,
    quantiles       TEXT NOT NULL
)
"""

# In-process cache: dimension -> (sorted quantile list, reverse, zero_perfect)
_cache: dict = {}


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_DDL)
    conn.commit()


def store_baseline(
    conn: sqlite3.Connection,
    dimension: str,
    values: Sequence[float],
    *,
    reverse: bool,
    zero_is_perfect: bool,
    n_quantiles: int = 1001,
) -> None:
    """Compute and persist the quantile grid for a dimension."""
    vals = sorted(float(v) for v in values if v is not None)
    if len(vals) < 100:
        raise ValueError(
            f"baseline for '{dimension}': only {len(vals)} samples — refusing "
            "to freeze a distribution this thin"
        )
    n = len(vals)
    quantiles = [
        vals[min(n - 1, round(i * (n - 1) / (n_quantiles - 1)))]
        for i in range(n_quantiles)
    ]
    ensure_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO baseline_dist "
        "(dimension, updated_at, n, reverse, zero_is_perfect, quantiles) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            dimension,
            datetime.now(timezone.utc).isoformat(),
            n,
            int(reverse),
            int(zero_is_perfect),
            json.dumps(quantiles),
        ),
    )
    conn.commit()
    _cache.pop(dimension, None)
    log.info("baseline[%s]: stored %d-sample distribution", dimension, n)


def _load(conn: sqlite3.Connection, dimension: str):
    if dimension in _cache:
        return _cache[dimension]
    try:
        row = conn.execute(
            "SELECT quantiles, reverse, zero_is_perfect FROM baseline_dist "
            "WHERE dimension = ?",
            (dimension,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None  # table doesn't exist yet
    if row is None:
        _cache[dimension] = None
        return None
    quantiles = json.loads(row[0])
    entry = (quantiles, bool(row[1]), bool(row[2]))
    _cache[dimension] = entry
    return entry


def clear_cache() -> None:
    _cache.clear()


def percentile_of(quantiles: list, value: float) -> float:
    """Map a raw value to its percentile [0, 100] on a stored quantile grid.

    Linear interpolation between grid points; ties resolved at the midpoint
    of the tied run so a value equal to a long flat stretch (e.g. many zero
    cells) lands in the middle of that mass, not at its edge.
    """
    n = len(quantiles)
    lo = bisect.bisect_left(quantiles, value)
    hi = bisect.bisect_right(quantiles, value)
    if lo == 0 and hi == 0:
        return 0.0
    if lo >= n:
        return 100.0
    mid = (lo + hi) / 2.0
    return round(100.0 * mid / (n - 1), 1)


def baseline_scores(
    conn: sqlite3.Connection,
    dimension: str,
    values: Sequence[float],
    *,
    reverse: bool = False,
    zero_is_perfect: bool = False,
) -> Optional[list]:
    """Score raw values against the frozen citywide distribution.

    Returns a list of 0-100 scores, or ``None`` when no baseline exists yet
    (callers fall back to batch-relative scoring so the pipeline still runs
    before the first ``build_baseline.py``).

    ``reverse=True`` means lower raw values are better (complaints, crime).
    ``zero_is_perfect`` pins a raw value of exactly 0 to 100.0 under reverse
    semantics ("no complaints is perfect, regardless of distribution").
    """
    entry = _load(conn, dimension)
    if entry is None:
        return None
    quantiles, _, _ = entry

    scores = []
    for v in values:
        if v is None:
            scores.append(None)
            continue
        pct = percentile_of(quantiles, float(v))
        score = (100.0 - pct) if reverse else pct
        if zero_is_perfect and reverse and float(v) == 0.0:
            score = 100.0
        scores.append(round(score, 1))
    return scores


def baseline_mean(conn: sqlite3.Connection, dimension: str) -> Optional[float]:
    """Approximate citywide mean of the raw metric (from the quantile grid).

    NB: incident-rate distributions are right-skewed, so the mean sits well
    above the median — do NOT use this as an EB prior for "no records"
    buildings (it anchors them above the typical building and turns the
    score into a building-size proxy). Use ``baseline_median`` instead.
    """
    entry = _load(conn, dimension)
    if entry is None:
        return None
    quantiles, _, _ = entry
    return sum(quantiles) / len(quantiles)


def baseline_median(conn: sqlite3.Connection, dimension: str) -> Optional[float]:
    """Citywide median of the raw metric — the correct EB prior.

    A building with no records and little exposure shrinks toward the
    TYPICAL building (median), landing at a neutral ~50th percentile;
    only accumulated clean exposure (many units, still no records) earns
    a top score.
    """
    entry = _load(conn, dimension)
    if entry is None:
        return None
    quantiles, _, _ = entry
    return quantiles[len(quantiles) // 2]


_seasonal_cache: dict = {}


def seasonal_factor(conn: sqlite3.Connection, table: str, date_col: str) -> float:
    """Seasonal normalization factor for THIS month, for a dated table.

    Complaint streams are strongly seasonal (HEAT/HOT WATER: 23× Jan vs
    Jul; noise peaks in summer), so decayed 12-month sums scored in July
    differ systematically from the same block scored in January. Dividing
    a block's decayed sum by this factor (the current month's share of
    annual volume, normalized to mean 1.0) removes the month-of-scoring
    bias without touching the underlying data.

    Cached per (table, month). Returns 1.0 on any failure or thin data.
    """
    from datetime import date as _date

    month = _date.today().month
    key = (table, month)
    if key in _seasonal_cache:
        return _seasonal_cache[key]
    factor = 1.0
    try:
        rows = conn.execute(
            f"SELECT CAST(strftime('%m', [{date_col}]) AS INTEGER) AS m, "
            f"COUNT(*) FROM [{table}] "
            f"WHERE [{date_col}] IS NOT NULL GROUP BY m"
        ).fetchall()
        counts = {int(r[0]): r[1] for r in rows if r[0]}
        if len(counts) >= 10 and sum(counts.values()) >= 1000:
            mean = sum(counts.values()) / len(counts)
            factor = max(0.25, min(4.0, counts.get(month, mean) / mean))
    except Exception:
        pass
    _seasonal_cache[key] = factor
    return factor


# ── Statistical helpers used by scorers ──────────────────────────────

DECAY_HALF_LIFE_DAYS = 180.0


def decay_weight(date_str: str, today_ord: int) -> float:
    """Exponential recency weight (half-life 6 months) for an ISO date.

    A complaint last week counts ~1.0; one 6 months ago 0.5; a year ago 0.25.
    Unparseable dates get weight 0.5 (present but age unknown).
    """
    try:
        d = datetime.fromisoformat(date_str[:10]).toordinal()
    except (ValueError, TypeError):
        return 0.5
    age = max(0, today_ord - d)
    return 0.5 ** (age / DECAY_HALF_LIFE_DAYS)


def eb_rate(count: float, units: float, prior_rate: Optional[float], k: float = 5.0) -> float:
    """Empirical-Bayes shrunk per-unit rate with an exposure tiebreaker.

    Shrinks small-sample rates toward the citywide prior so one complaint
    against a 2-unit building doesn't read as a crisis:
        rate = (count + k * prior) / (units + k)
    With no prior available, falls back to the raw rate.

    Exposure tiebreaker: zero-record buildings with similar unit counts
    produce nearly identical shrunken rates — measured: 45% of listings
    landed in a single bedbug score decile as one giant tie clump. A
    microscopic monotonic term (-1e-6 · log1p(units)) spreads ties by
    evidence strength (300 clean units ranks above 6 clean units) while
    being far too small to reorder any pair with actual evidence
    differences.
    """
    import math

    units = max(units, 1.0)
    tiebreak = 1e-6 * math.log1p(units)
    if prior_rate is None:
        return count / units - tiebreak
    return (count + k * prior_rate) / (units + k) - tiebreak
