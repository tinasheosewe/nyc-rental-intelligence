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
    score = row.get("crime_score")
    pct = abs(round((1 - ratio) * 100))
    if pct < 5:
        return None
    if direction == "improving":
        if score is not None and score < 30:
            return Flag(type="yellow", text=f"High crime area, but down {pct}% over 6 months")
        return Flag(type="green", text=f"Crime down {pct}% over 6 months")
    if direction == "worsening":
        if score is not None and score >= 70:
            return Flag(type="yellow", text=f"Crime up {pct}% over 6 months (still low overall)")
        return Flag(type="red", text=f"Crime up {pct}% over 6 months")
    return None


def _noise_trend(row: dict) -> Optional[Flag]:
    direction = row.get("noise_trend_direction", "stable")
    ratio = row.get("noise_trend_ratio") or 1.0
    score = row.get("noise_score")
    pct = abs(round((ratio - 1) * 100))
    if pct < 5:
        return None
    if direction == "improving":
        if score is not None and score < 30:
            return Flag(type="yellow", text=f"Noisy area, but complaints down {pct}%")
        return Flag(type="green", text=f"Noise complaints down {pct}%")
    if direction == "worsening":
        if score is not None and score >= 70:
            return Flag(type="yellow", text=f"Noise complaints up {pct}% (still quiet overall)")
        return Flag(type="yellow", text=f"Noise complaints up {pct}%")
    return None


def _rent_stabilized(row: dict) -> Optional[Flag]:
    if row.get("rent_stabilized"):
        return Flag(type="green", text="Rent-stabilized unit")
    return None


def _transit(row: dict) -> Optional[Flag]:
    if row.get("transit_station_count") is None:
        return None  # no transit data
    count = row["transit_station_count"]
    if count >= 4:
        return Flag(type="green", text=f"{count} subway stations within walking distance")
    if count == 0:
        return Flag(type="red", text="No subway stations nearby")
    return None


def _violations(row: dict) -> Optional[Flag]:
    if row.get("building_violation_count") is None:
        return None  # no violation data
    count = row["building_violation_count"]
    units = max(row.get("building_unitsres") or 1, 1)
    per_unit = count / units
    if count == 0:
        return Flag(type="green", text="No open building violations")
    # Rate-aware: use per-unit rate for buildings with 20+ units
    if units >= 20:
        if per_unit >= 0.5:
            return Flag(type="red", text=f"{count} open DOB violations ({per_unit:.2f}/unit)")
        if per_unit >= 0.2:
            return Flag(type="yellow", text=f"{count} open DOB violations ({per_unit:.2f}/unit)")
        return None  # low per-unit rate, not flagworthy
    # Small buildings: raw count thresholds
    if count >= 3:
        return Flag(type="red", text=f"{count} open DOB violations")
    if count >= 1:
        return Flag(type="yellow", text=f"{count} open DOB violation(s)")
    return None


def _management(row: dict) -> Optional[Flag]:
    if row.get("mgmt_complaints") is None:
        return None  # no management data
    complaints = row["mgmt_complaints"]
    units = max(row.get("mgmt_owner_units") or 1, 1)
    per_unit = complaints / units
    if complaints == 0:
        return Flag(type="green", text="No HPD complaints on record")
    # Rate-aware: use per-unit rate for portfolios with 50+ units
    if units >= 50:
        if per_unit >= 0.3:
            return Flag(type="red", text=f"Management: {complaints} HPD complaints ({per_unit:.2f}/unit)")
        if per_unit >= 0.1:
            return Flag(type="yellow", text=f"Management: {complaints} HPD complaints ({per_unit:.2f}/unit)")
        return None  # low per-unit rate, not flagworthy
    # Small portfolios: raw count thresholds
    if complaints >= 10:
        return Flag(type="red", text=f"Management: {complaints} HPD complaints")
    if complaints >= 5:
        return Flag(type="yellow", text=f"Management: {complaints} HPD complaints")
    return None


def _flood_risk(row: dict) -> Optional[Flag]:
    # NB: `or 100` would treat the in-flood-zone score of exactly 0.0 as
    # missing and default it to 100 — the red flag could never fire.
    score = row.get("flood_risk_score")
    if score is None:
        score = 100
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
    if row.get("deal_score") is None:
        return None  # no deal data
    score = row["deal_score"]
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
    tenure = row.get("tenure_median_months")

    # If we computed tenure, use it for a more informative flag
    if tenure is not None and tenure > 0:
        if tenure <= 6:
            return Flag(type="red", text=f"High turnover — avg tenant ~{tenure:.0f} mo")
        if tenure <= 9:
            return Flag(type="yellow", text=f"Short tenure — avg tenant ~{tenure:.0f} mo")
        if tenure >= 24:
            return Flag(type="green", text=f"Stable tenants — avg stay ~{tenure:.0f} mo")
        return None  # 9-24 months = normal, no flag

    # Fallback to relist count when tenure can't be computed
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
    if row.get("pest_hpd_count") is None and row.get("pest_rodent_count") is None:
        return None  # no pest data
    hpd = row.get("pest_hpd_count") or 0
    rodent = row.get("pest_rodent_count") or 0
    total = hpd + rodent
    units = max(row.get("pest_units") or row.get("building_unitsres") or 1, 1)
    if total == 0:
        return Flag(type="green", text="No pest or rodent complaints")
    # Rate-aware for large buildings
    if units >= 50:
        per_unit = hpd / units
        if per_unit >= 0.05:  # 5%+ units with pest complaints
            return Flag(type="red", text=f"{hpd} HPD pest complaints ({per_unit:.1%} of units)")
        if per_unit >= 0.02:
            return Flag(type="yellow", text=f"Some pest activity ({hpd} building, {rodent} area)")
        # Low building rate but check area rodents separately
        if rodent >= 10:
            return Flag(type="yellow", text=f"{rodent} rodent complaints in area")
        return None  # negligible rate
    # Small buildings: raw count thresholds
    if hpd >= 5:
        return Flag(type="red", text=f"{hpd} HPD pest complaints in building")
    if total >= 10:
        return Flag(type="red", text=f"High pest activity ({hpd} building, {rodent} area)")
    if total >= 3:
        return Flag(type="yellow", text=f"Some pest activity ({hpd} building, {rodent} area)")
    return None


def _highway_adjacent(row: dict) -> Optional[Flag]:
    dist = row.get("road_exposure_hwy_dist_m")
    if dist is None:
        return None
    if dist <= 150:
        return Flag(
            type="red",
            text=f"~{int(round(dist))}m from a highway — expect constant traffic noise",
        )
    return None


def _truck_route(row: dict) -> Optional[Flag]:
    # truck component >= 1.5 means the nearest designated truck-route
    # point is within ~42 m (3 * exp(-d/60) >= 1.5).
    truck = row.get("road_exposure_truck")
    if truck is not None and truck >= 1.5:
        return Flag(type="yellow", text="On or beside a designated truck route")
    return None


def _hpd_class_c(row: dict) -> Optional[Flag]:
    if row.get("building_hpd_class_c") is None:
        return None  # no data
    c = row["building_hpd_class_c"]
    units = max(row.get("building_unitsres") or 1, 1)
    if c == 0:
        return None
    # Rate-aware for large buildings
    if units >= 50:
        per_unit = c / units
        if per_unit >= 0.02:  # 2%+ units with hazardous violations
            return Flag(type="red", text=f"{c} hazardous (Class C) HPD violations ({per_unit:.1%} of units)")
        if per_unit >= 0.005:  # 0.5-2%
            return Flag(type="yellow", text=f"{c} hazardous (Class C) HPD violation(s)")
        return None  # negligible rate
    # Small buildings: any Class C is concerning
    if c >= 3:
        return Flag(type="red", text=f"{c} hazardous (Class C) HPD violations")
    if c >= 1:
        return Flag(type="yellow", text=f"{c} hazardous (Class C) HPD violation(s)")
    return None


def _litigations(row: dict) -> Optional[Flag]:
    if row.get("mgmt_litigations") is None:
        return None  # no data
    n = row["mgmt_litigations"]
    if n >= 1:
        return Flag(type="red", text=f"Building has {n} open HPD litigation(s)")
    return None


def _evictions(row: dict) -> Optional[Flag]:
    if row.get("mgmt_evictions") is None:
        return None  # no data
    n = row["mgmt_evictions"]
    units = max(row.get("building_unitsres") or row.get("mgmt_owner_units") or 1, 1)
    if n == 0:
        return None
    # Rate-aware: for large buildings, evictions are normal at low rates
    if units >= 50:
        per_unit = n / units
        if per_unit >= 0.05:  # 5%+ of units had evictions
            return Flag(type="red", text=f"{n} eviction filings ({per_unit:.1%} of units)")
        if per_unit >= 0.02:  # 2-5%
            return Flag(type="yellow", text=f"{n} eviction filings ({per_unit:.1%} of units)")
        return None  # < 2% eviction rate in large building = not flagworthy
    # Small buildings: raw count
    if n >= 5:
        return Flag(type="red", text=f"{n} eviction filings at this building")
    if n >= 2:
        return Flag(type="yellow", text=f"{n} eviction filings nearby")
    return None


# ── Public API ──────────────────────────────────────────────────

def _building_data_states(row: dict) -> Optional[Flag]:
    """Surface honest building-data states instead of fake scores."""
    if any(
        row.get(f"{dim}_new_building")
        for dim in ("building_violations", "management", "bedbug", "pest")
    ):
        return Flag(type="yellow", text="New building — no compliance track record yet")
    if any(
        row.get(f"{dim}_match_uncertain")
        for dim in ("building_violations", "management", "bedbug", "pest")
    ):
        return Flag(type="yellow", text="Building records may not match this exact address")
    return None


def _bedbug_never_filed(row: dict) -> Optional[Flag]:
    units = row.get("building_unitsres") or 0
    if row.get("bedbug_never_filed") and units >= 5:
        return Flag(type="yellow", text="Owner has never filed the required annual bedbug report")
    return None


_RULES = [
    _crime_trend,
    _noise_trend,
    _rent_stabilized,
    _building_data_states,
    _bedbug_never_filed,
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
    _highway_adjacent,
    _truck_route,
]


def generate_flags(row: dict) -> list[Flag]:
    """Apply all flag rules to a listing row and return non-None results."""
    flags: list[Flag] = []
    for rule in _RULES:
        flag = rule(row)
        if flag is not None:
            flags.append(flag)
    return flags


# ── Forensic flags — from request-time building_facts lookups ────

def forensic_flags(forensics: dict | None) -> list[Flag]:
    """Flags derived from the building-forensics payload.

    Each topic may be None (dataset still downloading) — skip silently.
    """
    if not forensics:
        return []
    flags: list[Flag] = []

    vac = forensics.get("vacate_orders")
    if vac and vac.get("has_active_vacate_order"):
        reason = (vac.get("reason") or "unsafe conditions").lower()
        flags.append(Flag(type="red", text=f"Active HPD vacate order on this building ({reason})"))

    aep = forensics.get("aep")
    if aep and aep.get("in_aep"):
        flags.append(Flag(type="red", text="On HPD's Alternative Enforcement list (city-designated worst buildings)"))

    bb = forensics.get("bedbugs")
    if bb:
        if (bb.get("total_reinfested_units") or 0) > 0:
            flags.append(Flag(type="red", text="Repeat bedbug infestations reported by owner"))
        elif (bb.get("total_infested_units") or 0) > 0:
            flags.append(Flag(type="yellow", text=f"{int(bb['total_infested_units'])} unit(s) reported bedbug infestation"))
        elif (bb.get("filings") or 0) >= 2:
            flags.append(Flag(type="green", text="Owner bedbug filings on record — no infestations reported"))

    spec = forensics.get("speculation")
    if spec and spec.get("on_watch_list"):
        flags.append(Flag(type="yellow", text="Building on NYC Speculation Watch List (recent investor purchase)"))

    lien = forensics.get("tax_lien")
    if lien and (lien.get("entry_count") or 0) > 0:
        if lien.get("all_water_debt_only"):
            flags.append(Flag(type="yellow", text="Building appeared on tax-lien sale list (water debt)"))
        else:
            flags.append(Flag(type="red", text="Building appeared on tax-lien sale list (financial distress)"))

    shed = forensics.get("sidewalk_shed")
    if shed and shed.get("has_active_shed"):
        age_days = shed.get("shed_age_days") or 0
        if age_days > 730:
            flags.append(Flag(type="yellow", text=f"Sidewalk shed up for {age_days // 365}+ years (facade work stalled?)"))
        else:
            flags.append(Flag(type="yellow", text="Active sidewalk shed / scaffolding at building"))

    ecb = forensics.get("ecb")
    if ecb and (ecb.get("total_balance_due") or 0) > 10_000:
        flags.append(Flag(type="red", text=f"${int(ecb['total_balance_due']):,} in unpaid DOB/ECB penalties"))

    erp = forensics.get("omo")
    if erp and (erp.get("order_count") or 0) >= 3:
        flags.append(Flag(type="red", text=f"{int(erp['order_count'])} city emergency-repair orders (owner failed to fix)"))

    reg = forensics.get("registration")
    if reg and reg.get("expired"):
        flags.append(Flag(type="yellow", text="Landlord's HPD registration has expired"))

    return flags
