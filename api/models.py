"""
Pydantic models for API responses.

Single source of truth for data shapes returned by the API.
Frontend TypeScript types mirror these models.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


# ── Nearby station with route badges ────────────────────────────

class TransitStation(BaseModel):
    """A subway station with distance and served route letters."""
    name: str
    distance_m: int
    routes: list[str] = []


# ── Categorized amenities ───────────────────────────────────────

class CategorizedAmenities(BaseModel):
    """Building amenities grouped by category."""
    services: list[str] = []       # doorman, elevator, laundry, etc.
    wellness: list[str] = []       # gym, pool, etc.
    outdoor: list[str] = []        # roof deck, garden, courtyard
    convenience: list[str] = []    # parking, storage, bike room
    unit_features: list[str] = []  # in-unit washer/dryer, dishwasher, etc.


# ── Neighborhood info ───────────────────────────────────────────

class NeighborhoodInfo(BaseModel):
    """About-the-neighborhood section with description and median prices."""
    description: Optional[str] = None
    median_rent_1br: Optional[int] = None
    median_rent_2br: Optional[int] = None


# ── POI (Point of Interest) ─────────────────────────────────────

class POI(BaseModel):
    """A nearby point of interest with category and distance."""
    name: str
    category: str  # "park", "school", "grocery", "pharmacy", "gym", "restaurant", "laundry"
    distance_m: int


# ── Open house ──────────────────────────────────────────────────

class OpenHouse(BaseModel):
    """An open house event."""
    date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None


# ── Comparable listing (compact) ────────────────────────────────

class ComparableListing(BaseModel):
    """Compact listing summary for similar/also-consider sections."""
    id: str
    address: str
    unit: Optional[str] = None
    neighborhood: str
    price: int
    beds: int
    baths: float
    sqft: Optional[int] = None
    photo: Optional[str] = None  # first photo URL
    composite_score: float = 0.0
    group_scores: dict[str, float] = {}  # value, access, neighborhood, safety, building
    better_in: Optional[str] = None  # group key where this listing excels (for "also consider")


class Scores(BaseModel):
    """All scoring dimensions plus rent-stabilized flag.

    Dimensions default to None (not 0) when no data is available.
    The frontend must treat None as "no data" — not as a zero score.
    """

    composite: float = 0.0
    deal: Optional[float] = None
    transit: Optional[float] = None
    crime: Optional[float] = None
    noise: Optional[float] = None
    building_violations: Optional[float] = None
    parks: Optional[float] = None
    schools: Optional[float] = None
    management: Optional[float] = None
    convenience: Optional[float] = None
    unit_amenities: Optional[float] = None
    shelter: Optional[float] = None
    pest: Optional[float] = None
    greenery: Optional[float] = None
    rent_stabilized: bool = False


class Trends(BaseModel):
    """Trend data for crime and noise."""

    crime_direction: str = "stable"
    crime_ratio: float = 1.0
    noise_direction: str = "stable"
    noise_ratio: float = 1.0


class Flag(BaseModel):
    """A single insight flag (green/red/yellow)."""

    type: str  # "green" | "red" | "yellow"
    text: str


class PriceHistoryEntry(BaseModel):
    """A single row from a listing's price history table."""

    date: str
    price: str
    event: str


class BuildingInfo(BaseModel):
    """Building-level metadata backing management/violation scores."""

    owner: Optional[str] = None
    year_built: Optional[int] = None
    total_units: Optional[int] = None
    stories: Optional[int] = None
    open_violations: int = 0
    total_violations: int = 0
    hpd_complaints_12mo: int = 0


class Listing(BaseModel):
    """Full listing payload with scores, trends, flags, and building info."""

    id: str
    address: str
    unit: Optional[str] = None
    neighborhood: str
    borough: str
    price: int
    beds: int
    baths: float
    sqft: Optional[int] = None
    photos: list[str] = []
    latitude: float
    longitude: float
    url: Optional[str] = None
    no_fee: bool = False
    days_on_market: Optional[int] = None
    available_at: Optional[str] = None
    description: Optional[str] = None
    amenities: list[str] = []
    price_history: list[PriceHistoryEntry] = []
    relist_count: int = 0

    scores: Scores = Scores()
    data_quality: Optional[str] = None
    score_components: dict[str, dict[str, Any]] = {}
    trends: Trends = Trends()
    flags: list[Flag] = []
    building: BuildingInfo = BuildingInfo()

    # ── New listing-detail fields ────────────────────────────────────
    pet_policy: Optional[str] = None           # e.g. "Cats and dogs allowed"
    categorized_amenities: CategorizedAmenities = CategorizedAmenities()
    transit_stations: list[TransitStation] = []  # nearby stations with route badges
    neighborhood_info: NeighborhoodInfo = NeighborhoodInfo()
    nearby_pois: list[POI] = []                 # parks, schools, grocery, etc.
    open_houses: list[OpenHouse] = []
    nearby_neighborhoods: list[str] = []        # adjacent neighborhood names
    similar: list[ComparableListing] = []       # 5 similar listings
    also_consider: list[ComparableListing] = [] # 5 also-consider listings (each better in 1 group)


class ListingsResponse(BaseModel):
    """Paginated listings response."""

    listings: list[Listing]
    total: int
    page: int
    page_size: int
