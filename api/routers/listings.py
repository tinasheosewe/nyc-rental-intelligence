"""
Listings API router.

Endpoints:
    GET /api/listings          — paginated, filterable, sortable listing feed
    GET /api/listings/{id}     — single listing detail
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query

from apthunt.db import get_connection
from api.models import (
    BuildingInfo,
    Flag,
    Listing,
    ListingsResponse,
    PriceHistoryEntry,
    Scores,
    Trends,
)
from api.flags import generate_flags
from api.composite import compute_composite, SCORE_KEYS

router = APIRouter(tags=["listings"])


# ── Helpers ─────────────────────────────────────────────────────

_PHOTO_PREFIX = "https://photos.example.com/"


def _days_on_market(first_seen: str | None) -> int | None:
    """Compute days since first_seen_at, or None if missing."""
    if not first_seen:
        return None
    try:
        dt = datetime.fromisoformat(first_seen)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0, delta.days)
    except (ValueError, TypeError):
        return None


def _parse_photos(raw: Optional[str]) -> list[str]:
    """Parse photos JSON and rewrite source photo URLs to local API paths."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        return [
            f"/api/photos/{url.removeprefix(_PHOTO_PREFIX)}"
            if isinstance(url, str) and url.startswith(_PHOTO_PREFIX)
            else url
            for url in parsed
        ]
    except (json.JSONDecodeError, TypeError):
        return []


# ── Grade curve ─────────────────────────────────────────────────
#
# Raw scores are on a 0-100 scale where the median is ~50.
# Psychologically 50 reads as an F.  A concave power curve pushes
# the median into B-/C+ territory so averages "feel" acceptable
# and only genuinely bad areas look bad.
#
#   raw 0 → 0  |  25 → 55  |  50 → 76  |  75 → 90  |  100 → 100

_GRADE_EXPONENT = 0.4


def _grade_curve(raw: float) -> float:
    """Monotonic concave curve: raises the middle while keeping 0 and 100 fixed."""
    if raw <= 0:
        return 0.0
    if raw >= 100:
        return 100.0
    return round(100.0 * (raw / 100.0) ** _GRADE_EXPONENT, 1)


def _row_to_scores(row: dict) -> dict[str, float | None]:
    """Extract score values from a DB row into a flat dict.

    Applies the grade curve so the UI shows school-grade-like values.
    Returns None for dimensions where the DB value is NULL (no data),
    so that compute_group_scores can exclude them from averages.
    """
    result: dict[str, float | None] = {}
    for key in SCORE_KEYS:
        raw = row.get(f"{key}_score")
        result[key] = _grade_curve(float(raw)) if raw is not None else None
    return result


# Column map: dimension → list of (db_column, display_label) pairs
_COMPONENT_MAP: dict[str, list[tuple[str, str]]] = {
    "pest": [
        ("pest_hpd_count", "HPD pest complaints (building)"),
        ("pest_rodent_count", "311 rodent complaints (100 m)"),
        ("pest_total", "Total pest reports"),
        ("pest_units", "Residential units"),
        ("pest_per_unit", "Pests per unit"),
    ],
    "convenience": [
        ("convenience_grocery", "Grocery / convenience"),
        ("convenience_pharmacy", "Pharmacies"),
        ("convenience_gym", "Gyms / fitness"),
        ("convenience_laundry", "Laundromats"),
        ("convenience_dining", "Restaurants & cafés"),
        ("convenience_total", "Weighted total"),
    ],
    "deal": [
        ("comp_median", "Neighborhood median rent"),
        ("comp_set_size", "Comp set size"),
        ("comp_scope", "Comp scope"),
        ("comp_sqft_median", "Neighborhood median sqft"),
        ("price_per_sqft", "$/sqft"),
        ("tenure_median_months", "Est. tenant tenure (months)"),
        ("tenure_cycle_count", "Listing cycles"),
    ],
    "unit_amenities": [
        ("unit_amenities_premium", "Premium amenities"),
        ("unit_amenities_standard", "Standard amenities"),
        ("unit_amenities_total", "Weighted total"),
    ],
    "shelter": [
        ("shelter_count", "Shelters within 800 m"),
        ("shelter_nearest_m", "Nearest shelter (m)"),
        ("shelter_nearest_name", "Nearest shelter"),
        ("project_count", "NYCHA buildings within 800 m"),
        ("project_nearest_m", "Nearest project (m)"),
        ("project_nearest_name", "Nearest NYCHA development"),
    ],
    "crime": [
        ("crime_felony_count", "Felonies (12 mo)"),
        ("crime_misdemeanor_count", "Misdemeanors (12 mo)"),
        ("crime_violation_count", "Violations (12 mo)"),
        ("crime_weighted_total", "Weighted total"),
    ],
    "noise": [
        ("noise_complaint_count", "Noise complaints"),
    ],
    "building_violations": [
        ("building_violation_count", "Active DOB violations"),
        ("building_hpd_class_a", "HPD Class A violations"),
        ("building_hpd_class_b", "HPD Class B violations"),
        ("building_hpd_class_c", "HPD Class C violations (hazardous)"),
        ("building_active_permits", "Active DOB permits"),
        ("building_unitsres", "Residential units"),
        ("building_violations_per_unit", "Weighted violations per unit"),
    ],
    "transit": [
        ("transit_station_count", "Stations within 800 m"),
        ("transit_routes_served", "Unique routes"),
        ("transit_nearest_m", "Nearest station (m)"),
    ],
    "parks": [
        ("parks_distance_m", "Distance to best park (m)"),
        ("parks_name", "Best scoring park"),
        ("parks_acres", "Park size (acres)"),
    ],
    "management": [
        ("mgmt_owner", "Owner / management co."),
        ("mgmt_owner_buildings", "Owner portfolio (buildings)"),
        ("mgmt_owner_units", "Owner portfolio (units)"),
        ("mgmt_complaints", "HPD complaints (12 mo)"),
        ("mgmt_hpd_heat", "Heat / hot water complaints"),
        ("mgmt_hpd_plumbing", "Plumbing complaints"),
        ("mgmt_hpd_paint", "Paint / plaster complaints"),
        ("mgmt_hpd_safety", "Safety complaints"),
        ("mgmt_heat_complaints", "311 heat complaints (area)"),
        ("mgmt_litigations", "HPD litigations (open)"),
        ("mgmt_evictions", "Eviction filings (building)"),
        ("mgmt_complaints_per_unit", "Complaints per unit"),
    ],
    "greenery": [
        ("greenery_tree_count", "Street trees within 200 m"),
        ("greenery_canopy_score", "Canopy score (diameter-weighted)"),
        ("greenery_garden_count", "Community gardens within 500 m"),
    ],
}


def _row_to_components(row: dict) -> dict[str, dict[str, object]]:
    """Extract per-dimension component data from a DB row."""
    out: dict[str, dict[str, object]] = {}
    for dim, cols in _COMPONENT_MAP.items():
        entries: dict[str, object] = {}
        for db_col, label in cols:
            val = row.get(db_col)
            if val is not None:
                entries[db_col] = val
        if entries:
            out[dim] = entries
    return out


def _row_to_listing(
    row: dict,
    priorities: list[str] | None = None,
    exclude_schools: bool = False,
) -> Listing:
    """Convert a raw DB row dict into a Listing response model."""
    score_vals = _row_to_scores(row)
    composite, data_quality = compute_composite(score_vals, priorities, exclude_schools=exclude_schools)

    scores = Scores(
        composite=composite,
        deal=score_vals.get("deal"),
        transit=score_vals.get("transit"),
        crime=score_vals.get("crime"),
        noise=score_vals.get("noise"),
        building_violations=score_vals.get("building_violations"),
        parks=score_vals.get("parks"),
        schools=None if exclude_schools else score_vals.get("schools"),
        management=score_vals.get("management"),
        convenience=score_vals.get("convenience"),
        unit_amenities=score_vals.get("unit_amenities"),
        shelter=score_vals.get("shelter"),
        pest=score_vals.get("pest"),
        greenery=score_vals.get("greenery"),
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
        stories=row.get("building_stories"),
        open_violations=row.get("building_violation_count") or 0,
        total_violations=row.get("building_violation_count") or 0,
        hpd_complaints_12mo=row.get("mgmt_complaints") or 0,
    )

    components = _row_to_components(row)

    # Parse amenities
    raw_amenities = row.get("amenities")
    amenities: list[str] = []
    if raw_amenities:
        try:
            parsed_am = json.loads(raw_amenities)
            if isinstance(parsed_am, list):
                amenities = parsed_am
        except (json.JSONDecodeError, TypeError):
            pass

    # Parse price history
    raw_ph = row.get("price_history")
    price_history: list[PriceHistoryEntry] = []
    if raw_ph:
        try:
            parsed_ph = json.loads(raw_ph)
            if isinstance(parsed_ph, list):
                price_history = [PriceHistoryEntry(**entry) for entry in parsed_ph]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

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
        days_on_market=_days_on_market(row.get("first_seen_at")),
        available_at=row.get("available_at"),
        description=row.get("description"),
        amenities=amenities,
        price_history=price_history,
        relist_count=row.get("relist_count") or 0,
        scores=scores,
        data_quality=data_quality,
        score_components=components,
        trends=trends,
        flags=flags,
        building=building,
    )


# ── Sort column mapping ────────────────────────────────────────

_SORT_MAP: dict[str, str] = {
    "composite": "",  # computed — handled specially
    "price": "price",
    # Individual dimensions
    "deal": "deal_score",
    "transit": "transit_score",
    "crime": "crime_score",
    "noise": "noise_score",
    "building_violations": "building_violations_score",
    "parks": "parks_score",
    "schools": "schools_score",
    "management": "management_score",
    "convenience": "convenience_score",
    "unit_amenities": "unit_amenities_score",
    "shelter": "shelter_score",
    "pest": "pest_score",
    "greenery": "greenery_score",
    # Group-level sorts (average of member dimensions)
    "value": "(COALESCE(deal_score,0) + COALESCE(unit_amenities_score,deal_score)) / 2.0",
    "access": "transit_score",
    "neighborhood": "(COALESCE(convenience_score,0) + COALESCE(parks_score,0) + COALESCE(greenery_score,0) + COALESCE(schools_score,0)) / 4.0",
    "neighborhood_no_schools": "(COALESCE(convenience_score,0) + COALESCE(parks_score,0) + COALESCE(greenery_score,0)) / 3.0",
    "safety": "(COALESCE(crime_score,0) + COALESCE(noise_score,0) + COALESCE(shelter_score,0)) / 3.0",
    "building": "(COALESCE(building_violations_score,0) + COALESCE(management_score,0) + COALESCE(pest_score,0)) / 3.0",
}


# ── Score coverage SQL helper ──────────────────────────────────

_SCORE_DB_COLS = [f"{k}_score" for k in SCORE_KEYS]  # 13 columns


def _coverage_sql(exclude_schools: bool) -> str:
    """SQL expression that counts scored (non-NULL) dimensions."""
    cols = [c for c in _SCORE_DB_COLS if not (exclude_schools and c == "schools_score")]
    return "(" + " + ".join(f"CASE WHEN {c} IS NOT NULL THEN 1 ELSE 0 END" for c in cols) + ")"


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
    min_sqft: Optional[int] = Query(None, ge=0),
    available_before: Optional[str] = Query(None, description="ISO date; only listings available on or before"),
    amenities: Optional[str] = Query(None, description="Comma-separated required amenity names"),
    min_data_quality: Optional[str] = Query(None, description="Minimum data quality: 'full' or 'limited'"),
    priorities: Optional[str] = Query(None, description="Comma-separated group priority order"),
    kids_mode: Optional[bool] = Query(None, description="Include schools in scoring"),
) -> ListingsResponse:
    """Paginated listing feed with filtering and sorting."""
    exclude_schools = not kids_mode if kids_mode is not None else True
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

        if min_sqft is not None:
            conditions.append("sqft >= ?")
            params.append(min_sqft)

        if available_before:
            conditions.append("available_at IS NOT NULL AND available_at <= ?")
            params.append(available_before)

        # Amenity filter — SQL LIKE on JSON array column
        if amenities:
            for amenity in (a.strip() for a in amenities.split(",")):
                conditions.append('amenities LIKE ?')
                params.append(f'%"{amenity}"%')

        # Data-quality filter — minimum scored dimensions
        if min_data_quality in ("full", "limited"):
            n_dims = len([c for c in _SCORE_DB_COLS if not (exclude_schools and c == "schools_score")])
            threshold = 0.75 if min_data_quality == "full" else 0.25
            min_scored = math.ceil(threshold * n_dims)
            conditions.append(f"{_coverage_sql(exclude_schools)} >= ?")
            params.append(min_scored)

        where = " AND ".join(conditions)

        # Parse priority ordering for composite score
        priority_list: list[str] | None = None
        if priorities:
            priority_list = [p.strip() for p in priorities.split(",") if p.strip()]

        # Count total
        total = conn.execute(
            f"SELECT COUNT(*) FROM listings WHERE {where}", params
        ).fetchone()[0]

        # Determine sort
        sort_key = sort if sort in _SORT_MAP else "composite"
        # When schools excluded, redirect schools sort → neighborhood and
        # use the no-schools neighborhood formula.
        if exclude_schools:
            if sort_key == "schools":
                sort_key = "neighborhood_no_schools"
            elif sort_key == "neighborhood":
                sort_key = "neighborhood_no_schools"
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
            listings = [_row_to_listing(dict(r), priority_list, exclude_schools) for r in rows]
        else:
            # Composite sort — need to compute on all, then paginate
            rows = conn.execute(
                f"SELECT * FROM listings WHERE {where}", params
            ).fetchall()
            all_listings = [_row_to_listing(dict(r), priority_list, exclude_schools) for r in rows]

            # Filter by min_score if provided
            if min_score is not None:
                all_listings = [
                    lst for lst in all_listings
                    if lst.scores.composite >= min_score
                ]
                total = len(all_listings)

            # Tiebreak: when composites are equal, prefer more data coverage
            _DQ_RANK = {None: 2, "limited": 1, "very_limited": 0}
            all_listings.sort(
                key=lambda x: (x.scores.composite, _DQ_RANK.get(x.data_quality, 0)),
                reverse=True,
            )
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


@router.get("/neighborhoods", response_model=list[str])
def get_neighborhoods() -> list[str]:
    """Return sorted list of distinct neighborhoods with active listings."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT neighborhood FROM listings "
            "WHERE UPPER(status) = 'ACTIVE' AND neighborhood IS NOT NULL "
            "ORDER BY neighborhood"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


@router.get("/amenities", response_model=list[str])
def get_amenities() -> list[str]:
    """Return sorted list of distinct amenity names across active listings."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT amenities FROM listings "
            "WHERE UPPER(status) = 'ACTIVE' AND amenities IS NOT NULL AND amenities != '[]'"
        ).fetchall()
        all_amenities: set[str] = set()
        for row in rows:
            try:
                parsed = json.loads(row[0])
                if isinstance(parsed, list):
                    all_amenities.update(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(all_amenities)
    finally:
        conn.close()
