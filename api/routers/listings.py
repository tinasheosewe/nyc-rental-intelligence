"""
Listings API router.

Endpoints:
    GET /api/listings          — paginated, filterable, sortable listing feed
    GET /api/listings/{id}     — single listing detail
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query

from apthunt.db import get_connection
from api.models import (
    BuildingInfo,
    Flag,
    Listing,
    ListingsResponse,
    Scores,
    Trends,
)
from api.flags import generate_flags
from api.composite import compute_composite, SCORE_KEYS

router = APIRouter(tags=["listings"])


# ── Helpers ─────────────────────────────────────────────────────

def _parse_photos(raw: Optional[str]) -> list[str]:
    """Parse photos JSON string into a list of URLs."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def _row_to_scores(row: dict) -> dict[str, float]:
    """Extract score values from a DB row into a flat dict."""
    return {
        key: float(row.get(f"{key}_score") or 0)
        for key in SCORE_KEYS
    }


def _row_to_listing(row: dict, priorities: list[str] | None = None) -> Listing:
    """Convert a raw DB row dict into a Listing response model."""
    score_vals = _row_to_scores(row)
    composite = compute_composite(score_vals, priorities)

    scores = Scores(
        composite=composite,
        deal=score_vals.get("deal", 0),
        transit=score_vals.get("transit", 0),
        flood_risk=score_vals.get("flood_risk", 0),
        crime=score_vals.get("crime", 0),
        noise=score_vals.get("noise", 0),
        building_violations=score_vals.get("building_violations", 0),
        parks=score_vals.get("parks", 0),
        schools=score_vals.get("schools", 0),
        management=score_vals.get("management", 0),
        amenity=score_vals.get("amenity", 0),
        rent_stabilized=bool(row.get("rent_stabilized")),
    )

    trends = Trends(
        crime_direction=row.get("crime_trend_direction") or "stable",
        crime_ratio=float(row.get("crime_trend_ratio") or 1.0),
        noise_direction=row.get("noise_trend_direction") or "stable",
        noise_ratio=float(row.get("noise_trend_ratio") or 1.0),
    )

    flags = generate_flags(row)

    building = BuildingInfo(
        owner=row.get("mgmt_owner"),
        year_built=row.get("building_year"),
        total_units=row.get("building_unitsres"),
        open_violations=row.get("building_violation_count") or 0,
        total_violations=row.get("building_violation_count") or 0,
        hpd_complaints_12mo=row.get("mgmt_complaints") or 0,
    )

    return Listing(
        id=row["id"],
        address=row.get("address") or "Unknown",
        unit=row.get("unit"),
        neighborhood=row.get("neighborhood") or "Unknown",
        borough=row.get("borough") or "Unknown",
        price=row.get("price") or 0,
        beds=row.get("beds") or 0,
        baths=float(row.get("baths") or 1.0),
        sqft=row.get("sqft"),
        photos=_parse_photos(row.get("photos")),
        latitude=float(row.get("lat") or 0),
        longitude=float(row.get("lon") or 0),
        url=row.get("url"),
        no_fee=bool(row.get("no_fee")),
        scores=scores,
        trends=trends,
        flags=flags,
        building=building,
    )


# ── Sort column mapping ────────────────────────────────────────

_SORT_MAP: dict[str, str] = {
    "composite": "",  # computed — handled specially
    "price": "price",
    "deal": "deal_score",
    "transit": "transit_score",
    "flood_risk": "flood_risk_score",
    "crime": "crime_score",
    "noise": "noise_score",
    "building_violations": "building_violations_score",
    "parks": "parks_score",
    "schools": "schools_score",
    "management": "management_score",
    "amenity": "amenity_score",
}


# ── Endpoints ───────────────────────────────────────────────────

@router.get("/listings", response_model=ListingsResponse)
def get_listings(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: str = Query("composite"),
    beds: Optional[str] = Query(None, description="Comma-separated bed counts"),
    min_price: Optional[int] = Query(None, ge=0),
    max_price: Optional[int] = Query(None, ge=0),
    neighborhoods: Optional[str] = Query(None, description="Comma-separated"),
    rent_stabilized: Optional[bool] = Query(None),
    min_score: Optional[int] = Query(None, ge=0, le=100),
) -> ListingsResponse:
    """Paginated listing feed with filtering and sorting."""
    conn = get_connection()
    try:
        # Build WHERE clause
        conditions = ["UPPER(status) = 'ACTIVE'", "lat IS NOT NULL", "lon IS NOT NULL"]
        params: list = []

        if beds:
            bed_list = [int(b) for b in beds.split(",")]
            placeholders = ",".join("?" for _ in bed_list)
            conditions.append(f"beds IN ({placeholders})")
            params.extend(bed_list)

        if min_price is not None:
            conditions.append("price >= ?")
            params.append(min_price)

        if max_price is not None:
            conditions.append("price <= ?")
            params.append(max_price)

        if neighborhoods:
            nbrs = [n.strip() for n in neighborhoods.split(",")]
            placeholders = ",".join("?" for _ in nbrs)
            conditions.append(f"LOWER(neighborhood) IN ({placeholders})")
            params.extend(n.lower() for n in nbrs)

        if rent_stabilized is not None:
            conditions.append("rent_stabilized = ?")
            params.append(1 if rent_stabilized else 0)

        where = " AND ".join(conditions)

        # Count total
        total = conn.execute(
            f"SELECT COUNT(*) FROM listings WHERE {where}", params
        ).fetchone()[0]

        # Determine sort
        sort_key = sort if sort in _SORT_MAP else "composite"
        db_sort_col = _SORT_MAP.get(sort_key, "")

        if db_sort_col:
            order_clause = f"ORDER BY {db_sort_col} DESC"
        else:
            # composite — fetch all, sort in Python
            order_clause = ""

        # Fetch rows
        if db_sort_col:
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"SELECT * FROM listings WHERE {where} {order_clause} "
                f"LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()
            listings = [_row_to_listing(dict(r)) for r in rows]
        else:
            # Composite sort — need to compute on all, then paginate
            rows = conn.execute(
                f"SELECT * FROM listings WHERE {where}", params
            ).fetchall()
            all_listings = [_row_to_listing(dict(r)) for r in rows]

            # Filter by min_score if provided
            if min_score is not None:
                all_listings = [
                    lst for lst in all_listings
                    if lst.scores.composite >= min_score
                ]
                total = len(all_listings)

            all_listings.sort(key=lambda x: x.scores.composite, reverse=True)
            offset = (page - 1) * page_size
            listings = all_listings[offset : offset + page_size]

        return ListingsResponse(
            listings=listings,
            total=total,
            page=page,
            page_size=page_size,
        )
    finally:
        conn.close()


@router.get("/listings/{listing_id}", response_model=Listing)
def get_listing(listing_id: str) -> Listing:
    """Single listing detail."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Listing not found")
        return _row_to_listing(dict(row))
    finally:
        conn.close()
