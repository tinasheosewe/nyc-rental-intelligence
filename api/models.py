"""
Pydantic models for API responses.

Single source of truth for data shapes returned by the API.
Frontend TypeScript types mirror these models.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


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
    score_components: dict[str, dict[str, Any]] = {}
    trends: Trends = Trends()
    flags: list[Flag] = []
    building: BuildingInfo = BuildingInfo()


class ListingsResponse(BaseModel):
    """Paginated listings response."""

    listings: list[Listing]
    total: int
    page: int
    page_size: int
