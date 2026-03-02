"""
Flag generation logic.

Produces human-readable insight flags (green/red/yellow) from
listing scores and components.  Each rule is a simple function
that returns a Flag or None.
"""

from __future__ import annotations

from typing import Optional

from api.models import Flag


# ── Flag rules ──────────────────────────────────────────────────

def _crime_trend(row: dict) -> Optional[Flag]:
    direction = row.get("crime_trend_direction", "stable")
    ratio = row.get("crime_trend_ratio") or 1.0
    pct = abs(round((1 - ratio) * 100))
    if direction == "improving" and pct >= 5:
        return Flag(type="green", text=f"Crime down {pct}% over 6 months")
    if direction == "worsening" and pct >= 5:
        return Flag(type="red", text=f"Crime up {pct}% over 6 months")
    return None


def _noise_trend(row: dict) -> Optional[Flag]:
    direction = row.get("noise_trend_direction", "stable")
    ratio = row.get("noise_trend_ratio") or 1.0
    pct = abs(round((ratio - 1) * 100))
    if direction == "improving" and pct >= 5:
        return Flag(type="green", text=f"Noise complaints down {pct}%")
    if direction == "worsening" and pct >= 5:
        return Flag(type="yellow", text=f"Noise complaints up {pct}%")
    return None


def _rent_stabilized(row: dict) -> Optional[Flag]:
    if row.get("rent_stabilized"):
        return Flag(type="green", text="Rent-stabilized unit")
    return None


def _transit(row: dict) -> Optional[Flag]:
    count = row.get("transit_station_count") or 0
    if count >= 4:
        return Flag(type="green", text=f"{count} subway stations within walking distance")
    if count == 0:
        return Flag(type="red", text="No subway stations nearby")
    return None


def _violations(row: dict) -> Optional[Flag]:
    count = row.get("building_violation_count") or 0
    if count == 0:
        return Flag(type="green", text="No open building violations")
    elif count >= 3:
        return Flag(type="red", text=f"{count} open DOB violations")
    else:
        return Flag(type="yellow", text=f"{count} open DOB violation(s)")
    return None


def _management(row: dict) -> Optional[Flag]:
    complaints = row.get("mgmt_complaints") or 0
    if complaints == 0:
        return Flag(type="green", text="No HPD complaints on record")
    elif complaints >= 10:
        return Flag(type="red", text=f"Management: {complaints} HPD complaints")
    elif complaints >= 5:
        return Flag(type="yellow", text=f"Management: {complaints} HPD complaints")
    return None


def _flood_risk(row: dict) -> Optional[Flag]:
    score = row.get("flood_risk_score") or 100
    if score < 30:
        return Flag(type="red", text="Located in a FEMA flood zone")
    return None


def _parks(row: dict) -> Optional[Flag]:
    dist = row.get("parks_distance_m")
    name = row.get("parks_name")
    acres = row.get("parks_acres") or 0
    if dist is None or not name:
        return None
    # Flagship parks (100+ acres) get a flag if within ~500m
    if acres >= 100 and dist <= 500:
        return Flag(type="green", text=f"Near {name} ({int(acres)} acres, {dist}m)")
    # Large parks (15+ acres) within 300m
    if acres >= 15 and dist <= 300:
        return Flag(type="green", text=f"{name} within {dist}m ({int(acres)} acres)")
    # Any decent park right next door
    if acres >= 3 and dist <= 150:
        return Flag(type="green", text=f"{name} within {dist}m")
    return None


def _deal(row: dict) -> Optional[Flag]:
    score = row.get("deal_score") or 50
    ppsqft = row.get("price_per_sqft")
    if score >= 80:
        txt = "Priced well below neighborhood median"
        if ppsqft:
            txt += f" (${ppsqft:.0f}/sqft)"
        return Flag(type="green", text=txt)
    if score <= 20:
        txt = "Priced above neighborhood median"
        if ppsqft:
            txt += f" (${ppsqft:.0f}/sqft)"
        return Flag(type="red", text=txt)
    return None


def _relist(row: dict) -> Optional[Flag]:
    count = row.get("relist_count") or 0
    if count >= 3:
        return Flag(type="red", text=f"Relisted {count} times — possible issues")
    if count == 2:
        return Flag(type="yellow", text="Relisted twice")
    return None


def _no_fee(row: dict) -> Optional[Flag]:
    if row.get("no_fee"):
        return Flag(type="green", text="No broker fee")
    return None


def _shelter(row: dict) -> Optional[Flag]:
    count = row.get("shelter_count") or 0
    nearest = row.get("shelter_nearest_m") or 9999
    name = row.get("shelter_nearest_name") or ""
    if count >= 3 and nearest <= 200:
        return Flag(type="red", text=f"{count} homeless facilities within 800m (nearest: {nearest}m)")
    if count >= 1 and nearest <= 150:
        label = name[:40] if name else "Homeless facility"
        return Flag(type="yellow", text=f"{label} {nearest}m away")
    return None


def _projects(row: dict) -> Optional[Flag]:
    count = row.get("project_count") or 0
    nearest = row.get("project_nearest_m") or 9999
    name = row.get("project_nearest_name") or ""
    if count >= 10 and nearest <= 200:
        return Flag(type="red", text=f"{count} NYCHA buildings within 800m (nearest: {nearest}m)")
    if count >= 5 and nearest <= 300:
        label = name[:40] if name else "NYCHA project"
        return Flag(type="yellow", text=f"{label} — {count} bldgs nearby")
    return None


def _pest(row: dict) -> Optional[Flag]:
    hpd = row.get("pest_hpd_count") or 0
    rodent = row.get("pest_rodent_count") or 0
    total = hpd + rodent
    if total == 0:
        return Flag(type="green", text="No pest or rodent complaints")
    if hpd >= 5:
        return Flag(type="red", text=f"{hpd} HPD pest complaints in building")
    if total >= 10:
        return Flag(type="red", text=f"High pest activity ({hpd} building, {rodent} area)")
    if total >= 3:
        return Flag(type="yellow", text=f"Some pest activity ({hpd} building, {rodent} area)")
    return None


def _hpd_class_c(row: dict) -> Optional[Flag]:
    c = row.get("building_hpd_class_c") or 0
    if c >= 3:
        return Flag(type="red", text=f"{c} hazardous (Class C) HPD violations")
    if c >= 1:
        return Flag(type="yellow", text=f"{c} hazardous (Class C) HPD violation(s)")
    return None


def _litigations(row: dict) -> Optional[Flag]:
    n = row.get("mgmt_litigations") or 0
    if n >= 1:
        return Flag(type="red", text=f"Building has {n} open HPD litigation(s)")
    return None


def _evictions(row: dict) -> Optional[Flag]:
    n = row.get("mgmt_evictions") or 0
    if n >= 5:
        return Flag(type="red", text=f"{n} eviction filings at this building")
    if n >= 2:
        return Flag(type="yellow", text=f"{n} eviction filings nearby")
    return None


# ── Public API ──────────────────────────────────────────────────

_RULES = [
    _crime_trend,
    _noise_trend,
    _rent_stabilized,
    _transit,
    _violations,
    _hpd_class_c,
    _management,
    _litigations,
    _evictions,
    _flood_risk,
    _parks,
    _deal,
    _no_fee,
    _relist,
    _shelter,
    _projects,
    _pest,
]


def generate_flags(row: dict) -> list[Flag]:
    """Apply all flag rules to a listing row and return non-None results."""
    flags: list[Flag] = []
    for rule in _RULES:
        flag = rule(row)
        if flag is not None:
            flags.append(flag)
    return flags
