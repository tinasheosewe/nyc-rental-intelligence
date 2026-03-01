"""
Pydantic models for API responses.

Single source of truth for data shapes returned by the API.
Frontend TypeScript types mirror these models.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class Scores(BaseModel):
    """All 11 scoring dimensions plus rent-stabilized flag."""

    composite: float = 0.0
    deal: float = 0.0
    transit: float = 0.0
    flood_risk: float = 0.0
    crime: float = 0.0
    noise: float = 0.0
    building_violations: float = 0.0
    parks: float = 0.0
    schools: float = 0.0
    management: float = 0.0
    amenity: float = 0.0
    shelter: float = 0.0
    pest: float = 0.0
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


class BuildingInfo(BaseModel):
    """Building-level metadata backing management/violation scores."""

    owner: Optional[str] = None
    year_built: Optional[int] = None
    total_units: Optional[int] = None
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
