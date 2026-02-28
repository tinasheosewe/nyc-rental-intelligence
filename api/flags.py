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
    if dist is not None and dist <= 200 and name:
        return Flag(type="green", text=f"{name} within {dist}m")
    return None


def _deal(row: dict) -> Optional[Flag]:
    score = row.get("deal_score") or 50
    if score >= 80:
        return Flag(type="green", text="Priced well below neighborhood median")
    if score <= 20:
        return Flag(type="red", text="Priced above neighborhood median")
    return None


def _no_fee(row: dict) -> Optional[Flag]:
    if row.get("no_fee"):
        return Flag(type="green", text="No broker fee")
    return None


# ── Public API ──────────────────────────────────────────────────

_RULES = [
    _crime_trend,
    _noise_trend,
    _rent_stabilized,
    _transit,
    _violations,
    _management,
    _flood_risk,
    _parks,
    _deal,
    _no_fee,
]


def generate_flags(row: dict) -> list[Flag]:
    """Apply all flag rules to a listing row and return non-None results."""
    flags: list[Flag] = []
    for rule in _RULES:
        flag = rule(row)
        if flag is not None:
            flags.append(flag)
    return flags
