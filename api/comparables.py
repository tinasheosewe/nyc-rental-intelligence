"""
Comparable listings engine.

Finds "similar" and "also consider" listings for a given listing
using the 13-dimension scoring engine.

Similar (5):
    Same beds, nearby (same neighborhood with borough fallback),
    closest in overall score profile (cosine similarity on the
    5 group-score vector).

Also Consider (5):
    Same constraints, but each listing is similar on 4 groups
    yet *better* on a different 5th group. One per score group.
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


# ── Grade curve (duplicated to avoid circular import) ───────────

_GRADE_EXPONENT = 0.4


def _grade_curve(raw: float) -> float:
    if raw <= 0:
        return 0.0
    if raw >= 100:
        return 100.0
    return round(100.0 * (raw / 100.0) ** _GRADE_EXPONENT, 1)


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
    n_similar: int = 5,
    n_also_consider: int = 5,
) -> tuple[list[ComparableListing], list[ComparableListing]]:
    """
    Find similar and also-consider listings.

    Returns (similar, also_consider).
    """
    # Fetch candidates: same beds, nearby, active, not self
    # Try neighborhood first, fall back to borough if not enough candidates
    candidates = _fetch_candidates(conn, listing_id, beds, neighborhood, borough, lat, lon)

    if len(candidates) < 3:
        return [], []

    # Compute group scores for each candidate
    scored_candidates: list[tuple[dict, dict[str, float | None], float]] = []
    for row in candidates:
        gs = _row_to_group_scores(row)
        # Compute a simple composite from group scores
        vals = [v for v in gs.values() if v is not None]
        comp = sum(vals) / len(vals) if vals else 0.0
        scored_candidates.append((row, gs, round(comp, 1)))

    target_vec = _group_vector(target_group_scores, GROUP_KEYS)

    # ── Similar: rank by cosine similarity ──────────────────────
    similarities: list[tuple[float, int]] = []
    for i, (row, gs, comp) in enumerate(scored_candidates):
        vec = _group_vector(gs, GROUP_KEYS)
        sim = _cosine_similarity(target_vec, vec)
        similarities.append((sim, i))

    similarities.sort(key=lambda x: x[0], reverse=True)
    similar = [
        _row_to_comparable(scored_candidates[idx][0], scored_candidates[idx][1], scored_candidates[idx][2])
        for _, idx in similarities[:n_similar]
    ]

    # ── Also Consider: one per group, better in that group ──────
    used_ids = {s.id for s in similar}
    also_consider: list[ComparableListing] = []

    for group_key in GROUP_KEYS:
        target_group_val = target_group_scores.get(group_key) or 50.0
        best_candidate: tuple[float, int] | None = None

        for i, (row, gs, comp) in enumerate(scored_candidates):
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

            improvement = cand_group_val - target_group_val
            if best_candidate is None or improvement > best_candidate[0]:
                best_candidate = (improvement, i)

        if best_candidate is not None:
            idx = best_candidate[1]
            row, gs, comp = scored_candidates[idx]
            also_consider.append(
                _row_to_comparable(row, gs, comp, better_in=group_key)
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
    max_distance_deg: float = 0.03,  # ~3 km
) -> list[dict]:
    """Fetch candidate listings: same beds, nearby, active."""
    # Try same neighborhood first
    rows = conn.execute(
        """
        SELECT * FROM listings
        WHERE UPPER(status) = 'ACTIVE'
          AND id != ?
          AND beds = ?
          AND LOWER(neighborhood) = LOWER(?)
          AND lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY ABS(lat - ?) + ABS(lon - ?)
        LIMIT 50
        """,
        (listing_id, beds, neighborhood, lat, lon),
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
            ORDER BY ABS(lat - ?) + ABS(lon - ?)
            LIMIT 50
            """,
            (listing_id, beds, borough, lat, max_distance_deg, lon, max_distance_deg, lat, lon),
        ).fetchall()

    return [dict(r) for r in rows]
