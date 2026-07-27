"""Shared scoring utilities used across all scorers.

Centralises percentile ranking, median-inverse normalisation,
PLUTO helpers, BBL normalisation, geohash deduplication, and
trend analysis so that bug-fixes and improvements propagate
to every scorer automatically.
"""

from __future__ import annotations

from datetime import date, timedelta

from haversine import haversine, Unit


# ── Constants ────────────────────────────────────────────────────────

TREND_MIDPOINT: str = (date.today() - timedelta(days=182)).isoformat()
"""ISO-8601 date string ~6 months ago, used for trend bucketing."""


# ── Geohash helpers ──────────────────────────────────────────────────

def dedupe_by_geohash(
    listings: list[dict],
) -> dict[str, tuple[float, float]]:
    """Map each unique geohash to one (lat, lon) pair (first-seen wins)."""
    gh_map: dict[str, tuple[float, float]] = {}
    for lst in listings:
        gh_map.setdefault(lst["geohash"], (lst["lat"], lst["lon"]))
    return gh_map


# ── PLUTO / spatial helpers ──────────────────────────────────────────

def find_nearest_row(
    rows: list[dict],
    lat: float,
    lon: float,
    *,
    lat_key: str = "latitude",
    lon_key: str = "longitude",
    max_dist_m: float | None = None,
) -> dict | None:
    """Return the row nearest to *(lat, lon)* by Haversine distance.

    Works for any list of dicts that contain lat/lon columns.
    Rows missing valid coordinates are silently skipped.

    ``max_dist_m``: when set, return None if the nearest row is farther
    than this — used as an attribution guard so a listing whose geocode
    lands between lots can't inherit a *neighbor's* building record
    (measured: p50 match distance is 0.4m, p95 is 25m; beyond 40m the
    match is likely the wrong building). The matched row gains a
    "_dist_m" key either way.
    """
    if not rows:
        return None
    best: dict | None = None
    best_d = float("inf")
    for row in rows:
        try:
            rlat = float(row[lat_key])
            rlon = float(row[lon_key])
        except (KeyError, TypeError, ValueError):
            continue
        d = haversine((lat, lon), (rlat, rlon), unit=Unit.METERS)
        if d < best_d:
            best, best_d = row, d
    if best is not None:
        best["_dist_m"] = round(best_d, 1)
        if max_dist_m is not None and best_d > max_dist_m:
            return None
    return best


BUILDING_MATCH_MAX_M = 40.0
NEW_BUILDING_YEARS = 3


def is_new_building(pluto_row: dict | None) -> bool:
    """True when the building is too new to have a meaningful compliance
    record (~3 years) — scored dimensions should return None ("no track
    record yet"), not a fake perfect score."""
    if pluto_row is None:
        return False
    try:
        year = int(float(pluto_row.get("yearbuilt") or 0))
    except (TypeError, ValueError):
        return False
    return year >= date.today().year - NEW_BUILDING_YEARS


def pluto_units(row: dict | None, *, min_val: int = 1) -> int:
    """Extract residential-unit count from a PLUTO row, clamped to *min_val*."""
    if row is None:
        return max(min_val, 1)
    try:
        return max(min_val, int(float(row.get("unitsres") or 0)))
    except (ValueError, TypeError):
        return max(min_val, 1)


def normalize_bbl(raw) -> str:
    """Normalise a PLUTO BBL value to a plain integer string.

    ``"1234567890.00000000"`` → ``"1234567890"``.
    """
    try:
        return str(int(float(raw)))
    except (ValueError, TypeError):
        return str(raw)


def parse_bbl(raw) -> tuple[str, str, str]:
    """Parse a 10-digit BBL into *(boro, block, lot)*.

    PLUTO BBL layout: ``boro(1) + block(5) + lot(4)``.
    DOB stores lot as 5 digits, so we zero-pad.
    """
    bbl_str = str(int(float(raw))).zfill(10)
    boro = bbl_str[0]
    block = bbl_str[1:6]
    lot = bbl_str[6:10].zfill(5)
    return boro, block, lot


def kernel_weighted_units(
    store,
    lat: float,
    lon: float,
    radius_m: float,
    *,
    floor: float = 25.0,
) -> float:
    """Gaussian-kernel-weighted residential units around a point (PLUTO).

    The denominator for per-household incident rates: raw counts-in-radius
    are population-density confounded (dense Williamsburg generates more
    incident *volume* than sparse Bayside at identical per-person risk).
    Dividing kernel-weighted incidents by kernel-weighted units — the same
    kernel on both sides — yields a density-corrected rate.

    Floored so near-empty areas can't produce explosive rates.
    """
    import math

    sigma = radius_m / 2.0
    rows = store.query_circle(
        "pluto", lat=lat, lon=lon, radius_m=radius_m,
        select="unitsres",
    )
    total = 0.0
    for r in rows:
        try:
            units = float(r.get("unitsres") or 0)
        except (TypeError, ValueError):
            continue
        if units <= 0:
            continue
        d = float(r.get("_dist_m") or 0.0)
        total += units * math.exp(-((d / sigma) ** 2))
    return max(total, floor)


# Severance classes: roads that impede pedestrians crossing them, with a
# per-encounter walking penalty in effective meters. (At-grade highway
# crossings are rare — encounters there usually mean service roads and
# overpass approaches, still hostile.)
SEVERANCE_PENALTY_M = {
    "motorway": 250.0, "motorway_link": 200.0,
    "trunk": 250.0, "trunk_link": 200.0,
    "primary": 120.0, "secondary": 60.0,
}


# Optional fast path: a spatial_index.SeveranceRaster attached either on
# the DataStore (attach_fast_path) or module-wide via set_severance_raster.
# When present, path_severance_penalty_m answers from the raster in O(path
# length) array lookups instead of ~25 SQLite circle queries; any failure
# degrades to the SQLite implementation below.
_SEVERANCE_RASTER = None


def set_severance_raster(raster) -> None:
    """Install (or clear, with None) the module-wide severance raster."""
    global _SEVERANCE_RASTER
    _SEVERANCE_RASTER = raster


def path_severance_penalty_m(store, lat1, lon1, lat2, lon2) -> float:
    """Pedestrian-severance penalty (effective extra meters) for the
    straight path between two points.

    A park or supermarket 200m away across a six-lane arterial is not
    experientially 200m away — the sibling of acoustic shielding, on foot.
    Samples the path every ~20m; severing-class road points (ds_roads)
    within ~22m of a sample mark an encounter; consecutive blocked samples
    merge into one encounter (one crossing), each adding its class penalty.

    Returns 0.0 when ds_roads is missing (degrade gracefully).

    Uses the rasterized fast path when one is attached (store attribute
    first, module-level fallback); SQLite otherwise.
    """
    raster = getattr(store, "severance_raster", None) or _SEVERANCE_RASTER
    if raster is not None:
        try:
            return raster.path_penalty_m(lat1, lon1, lat2, lon2)
        except Exception:
            pass  # degrade to the SQLite reference path
    return _path_severance_penalty_m_sql(store, lat1, lon1, lat2, lon2)


def _path_severance_penalty_m_sql(store, lat1, lon1, lat2, lon2) -> float:
    """SQLite reference implementation (see path_severance_penalty_m)."""
    import math as _m

    dy = (lat2 - lat1) * 111_320.0
    dx = (lon2 - lon1) * 85_000.0
    seg = _m.hypot(dx, dy)
    if seg <= 40.0:
        return 0.0
    n = max(1, int(seg / 20.0))
    penalty = 0.0
    in_row_class = None
    for i in range(1, n):
        f = i / n
        plat = lat1 + f * (lat2 - lat1)
        plon = lon1 + f * (lon2 - lon1)
        try:
            rows = store.query_circle(
                "roads", lat=plat, lon=plon, radius_m=22.0,
                select="road_class", lat_col="lat", lon_col="lon",
            )
        except Exception:
            return 0.0  # roads table unavailable
        worst = None
        for r in rows:
            cls = r.get("road_class") or ""
            p = SEVERANCE_PENALTY_M.get(cls)
            if p is not None and (worst is None or p > worst[1]):
                worst = (cls, p)
        if worst and in_row_class is None:
            penalty += worst[1]
            in_row_class = worst[0]
        elif not worst:
            in_row_class = None
    return penalty


# ── Scoring functions ────────────────────────────────────────────────

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


def median_inverse_scores(
    values: list[float],
    *,
    baseline: list[float] | None = None,
) -> list[float]:
    """Median-normalise then invert: fewer/lower → higher score.

    Scale::

        value = 0       → score = 100
        value = median   → score = 50
        value ≥ 2×median → score = 0

    Args:
        values:   One raw value per listing (determines output length).
        baseline: Values used to compute the median.  Defaults to
                  *values* itself.  Pass the list of **unique-geohash**
                  values to preserve the current per-block median logic.
    """
    if not values:
        return []

    basis = baseline if baseline is not None else values
    sorted_vals = sorted(basis)
    n = len(sorted_vals)
    if n == 0:
        median = 1.0
    else:
        median = (
            sorted_vals[n // 2]
            if n % 2 == 1
            else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
        )
        if median == 0:
            median = 1.0

    scores: list[float] = []
    for v in values:
        if v <= median:
            sc = 100.0 - 50.0 * (v / median) if median > 0 else 100.0
        else:
            sc = max(0.0, 50.0 - 50.0 * ((v - median) / median))
        scores.append(sc)
    return scores


def compute_trend(recent: float, older: float) -> tuple[float, str]:
    """Compute trend ratio and classify direction.

    Returns:
        ``(ratio, direction)`` where *direction* is one of
        ``"improving"`` (< 0.85), ``"stable"``, ``"worsening"`` (> 1.15).
    """
    if older > 0:
        ratio = round(recent / older, 3)
    elif recent > 0:
        ratio = 2.0
    else:
        ratio = 1.0

    if ratio < 0.85:
        direction = "improving"
    elif ratio > 1.15:
        direction = "worsening"
    else:
        direction = "stable"
    return ratio, direction
