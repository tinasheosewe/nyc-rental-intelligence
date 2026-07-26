"""
Comparable listings engine.

Finds "similar" and "also consider" listings for a given listing
using the 13-dimension scoring engine.

Similar (5):
    Same beds, nearby (same neighborhood with borough fallback),
    ranked by a blend of cosine similarity, geographic proximity,
    and price proximity so results are as close as possible to the
    viewed listing in every dimension.

Also Consider (5):
    Same constraints, but each listing is similar on 4 groups
    yet *better* on a different 5th group. One per score group.
    Among qualifying candidates, picks the closest by a blend of
    distance and price proximity.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Optional

from api.composite import SCORE_GROUPS, compute_group_scores
from api.models import ComparableListing


# ── Vector helpers ──────────────────────────────────────────────

def _group_vector(
    group_scores: dict[str, float | None],
    group_keys: list[str],
) -> list[float]:
    """Build a numeric vector from group scores, filling None with 50."""
    return [group_scores.get(k) or 50.0 for k in group_keys]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0-1."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance between two lat/lon points in kilometres."""
    R = 6371.0  # Earth radius km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Grade curve (duplicated to avoid circular import) ───────────

# Grade curve removed: scores are citywide percentiles, reported as-is.
def _grade_curve(raw: float) -> float:
    if raw <= 0:
        return 0.0
    if raw >= 100:
        return 100.0
    return round(raw, 1)


# ── Main engine ────────────────────────────────────────────────

GROUP_KEYS = list(SCORE_GROUPS.keys())  # value, access, neighborhood, safety, building

SCORE_COLS = [
    "deal_score", "unit_amenities_score", "transit_score",
    "crime_score", "noise_score", "building_violations_score",
    "parks_score", "schools_score", "management_score",
    "convenience_score", "shelter_score", "pest_score", "greenery_score",
]


def _row_to_group_scores(row: dict) -> dict[str, float | None]:
    """Extract raw scores from a DB row, apply grade curve, compute group scores."""
    dim_scores: dict[str, float | None] = {}
    for col in SCORE_COLS:
        dim = col.removesuffix("_score")
        raw = row.get(col)
        dim_scores[dim] = _grade_curve(float(raw)) if raw is not None else None
    return compute_group_scores(dim_scores, exclude_schools=True)


def _row_to_comparable(
    row: dict,
    group_scores: dict[str, float | None],
    composite: float,
    better_in: str | None = None,
    distance_km: float | None = None,
    photo_prefix: str = "https://photos.example.com/",
) -> ComparableListing:
    """Convert a DB row to a ComparableListing."""
    import json
    photos = []
    raw_photos = row.get("photos")
    if raw_photos:
        try:
            parsed = json.loads(raw_photos)
            if isinstance(parsed, list) and parsed:
                url = parsed[0]
                if isinstance(url, str) and url.startswith(photo_prefix):
                    photos = [f"/api/photos/{url.removeprefix(photo_prefix)}"]
                else:
                    photos = [url] if isinstance(url, str) else []
        except (json.JSONDecodeError, TypeError):
            pass

    return ComparableListing(
        id=row["id"],
        address=row.get("address") or "Unknown",
        unit=row.get("unit"),
        neighborhood=row.get("neighborhood") or "Unknown",
        price=row.get("price") or 0,
        beds=row.get("beds") or 0,
        baths=float(row.get("baths") or 1.0),
        sqft=row.get("sqft"),
        photo=photos[0] if photos else None,
        composite_score=composite,
        group_scores={k: round(v, 1) for k, v in group_scores.items() if v is not None},
        better_in=better_in,
        distance_km=round(distance_km, 2) if distance_km is not None else None,
    )


def find_comparable_listings(
    conn: sqlite3.Connection,
    listing_id: str,
    beds: int,
    neighborhood: str,
    borough: str,
    lat: float,
    lon: float,
    target_group_scores: dict[str, float | None],
    target_composite: float,
    target_price: int = 0,
    n_similar: int = 5,
    n_also_consider: int = 5,
) -> tuple[list[ComparableListing], list[ComparableListing]]:
    """
    Find similar and also-consider listings.

    Rankings blend cosine similarity, geographic proximity, and price
    proximity so results are as close to the viewed listing as possible
    in location, price, and score profile.

    Returns (similar, also_consider).
    """
    candidates = _fetch_candidates(
        conn, listing_id, beds, neighborhood, borough, lat, lon,
        target_price=target_price,
    )

    if len(candidates) < 3:
        return [], []

    # Compute group scores, haversine distance, & price ratio for each candidate
    scored: list[tuple[dict, dict[str, float | None], float, float, float]] = []
    for row in candidates:
        gs = _row_to_group_scores(row)
        vals = [v for v in gs.values() if v is not None]
        comp = sum(vals) / len(vals) if vals else 0.0
        dist = _haversine_km(lat, lon, float(row["lat"]), float(row["lon"]))
        cand_price = row.get("price") or 0
        if target_price > 0 and cand_price > 0:
            price_ratio = abs(cand_price - target_price) / target_price
        else:
            price_ratio = 0.0
        scored.append((row, gs, round(comp, 1), dist, price_ratio))

    target_vec = _group_vector(target_group_scores, GROUP_KEYS)

    # ── Similar: rank by proximity + price weighted cosine sim ──
    #
    # combined = cosine_sim * proximity_weight * price_weight
    #
    # proximity_weight  = 1 / (1 + dist_km * 3)
    #   0 km → 1.0, 0.3 km → 0.53, 1 km → 0.25
    #
    # price_weight      = 1 / (1 + price_ratio * 2)
    #   0% diff → 1.0, 10% → 0.83, 25% → 0.67, 50% → 0.50
    #
    ranked: list[tuple[float, int]] = []
    for i, (row, gs, comp, dist, price_ratio) in enumerate(scored):
        vec = _group_vector(gs, GROUP_KEYS)
        cos = _cosine_similarity(target_vec, vec)
        proximity_w = 1.0 / (1.0 + dist * 3.0)
        price_w = 1.0 / (1.0 + price_ratio * 2.0)
        combined = cos * proximity_w * price_w
        ranked.append((combined, i))

    ranked.sort(key=lambda x: x[0], reverse=True)
    similar = [
        _row_to_comparable(
            scored[idx][0], scored[idx][1], scored[idx][2],
            distance_km=scored[idx][3],
        )
        for _, idx in ranked[:n_similar]
    ]

    # ── Also Consider: one per group, pick best by dist+price ───
    used_ids = {s.id for s in similar}
    also_consider: list[ComparableListing] = []

    for group_key in GROUP_KEYS:
        target_group_val = target_group_scores.get(group_key) or 50.0
        best_candidate: tuple[float, int] | None = None  # (penalty, idx)

        for i, (row, gs, comp, dist, price_ratio) in enumerate(scored):
            if row["id"] in used_ids:
                continue

            cand_group_val = gs.get(group_key) or 50.0

            # Must be better in this group
            if cand_group_val <= target_group_val:
                continue

            # Must be somewhat similar in other groups (cosine on other 4)
            other_target = [target_group_scores.get(k) or 50.0 for k in GROUP_KEYS if k != group_key]
            other_cand = [gs.get(k) or 50.0 for k in GROUP_KEYS if k != group_key]
            other_sim = _cosine_similarity(other_target, other_cand)

            if other_sim < 0.85:
                continue

            # Combined penalty: distance (km) + price deviation
            # Normalise so distance and price contribute roughly equally
            penalty = dist + price_ratio * 3.0  # 30% price diff ≈ 0.9 km
            if best_candidate is None or penalty < best_candidate[0]:
                best_candidate = (penalty, i)

        if best_candidate is not None:
            idx = best_candidate[1]
            row, gs, comp, dist, _ = scored[idx]
            also_consider.append(
                _row_to_comparable(row, gs, comp, better_in=group_key, distance_km=dist)
            )
            used_ids.add(row["id"])

    return similar, also_consider


def _fetch_candidates(
    conn: sqlite3.Connection,
    listing_id: str,
    beds: int,
    neighborhood: str,
    borough: str,
    lat: float,
    lon: float,
    target_price: int = 0,
    max_distance_deg: float = 0.03,  # ~3 km
    price_band: float = 0.50,  # ±50% of target price
) -> list[dict]:
    """Fetch candidate listings: same beds, nearby, similar price, active."""
    price_lo = int(target_price * (1 - price_band)) if target_price > 0 else 0
    price_hi = int(target_price * (1 + price_band)) if target_price > 0 else 999999999

    # Try same neighborhood first
    rows = conn.execute(
        """
        SELECT * FROM listings
        WHERE UPPER(status) = 'ACTIVE'
          AND id != ?
          AND beds = ?
          AND LOWER(neighborhood) = LOWER(?)
          AND lat IS NOT NULL AND lon IS NOT NULL
          AND price BETWEEN ? AND ?
        ORDER BY ABS(lat - ?) + ABS(lon - ?)
        LIMIT 50
        """,
        (listing_id, beds, neighborhood, price_lo, price_hi, lat, lon),
    ).fetchall()

    # If not enough in neighborhood, expand to borough + nearby
    if len(rows) < 12:
        rows = conn.execute(
            """
            SELECT * FROM listings
            WHERE UPPER(status) = 'ACTIVE'
              AND id != ?
              AND beds = ?
              AND LOWER(borough) = LOWER(?)
              AND lat IS NOT NULL AND lon IS NOT NULL
              AND ABS(lat - ?) < ? AND ABS(lon - ?) < ?
              AND price BETWEEN ? AND ?
            ORDER BY ABS(lat - ?) + ABS(lon - ?)
            LIMIT 50
            """,
            (listing_id, beds, borough, lat, max_distance_deg, lon, max_distance_deg,
             price_lo, price_hi, lat, lon),
        ).fetchall()

    return [dict(r) for r in rows]
