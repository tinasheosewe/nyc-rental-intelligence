"""
Deterministic one-sentence score explanations.

For each scored dimension, builds ONE plain-English sentence explaining
WHY the score is what it is — leading with the concrete fact (counts,
distances, rates from the listing's component columns), optionally
adding a citywide framing derived from the score itself (scores are
citywide percentiles, higher = better), and ending with neighborhood
peer context when available.

Rules:
    * Only fields actually present in the row are used — nothing is
      fabricated.  Missing components degrade to a shorter sentence or
      a generic percentile framing.
    * Numbers are formatted for humans (no raw floats).
    * Output: {dim: sentence} for every dim in SCORE_KEYS whose
      ``<dim>_score`` is non-null.
"""

from __future__ import annotations

from typing import Optional

from api.composite import SCORE_KEYS


# ── Formatting helpers ──────────────────────────────────────────

def _num(v) -> str:
    """Integer-style human number: 1,234."""
    return f"{int(round(float(v))):,}"


def _flt(v, nd: int = 1) -> str:
    """Float rounded to nd places with trailing zeros stripped."""
    s = f"{float(v):.{nd}f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _dist(m) -> str:
    """Metres → '240 m' / '1.2 km'."""
    m = float(m)
    if m >= 1000:
        return f"{_flt(m / 1000.0, 1)} km"
    return f"{_num(m)} m"


def _plural(n, singular: str, plural: Optional[str] = None) -> str:
    n = int(round(float(n)))
    word = singular if n == 1 else (plural or singular + "s")
    return f"{_num(n)} {word}"


def _title_owner(name: str) -> str:
    """ALL-CAPS owner name → title case, preserving common acronyms."""
    out = name.title()
    for acr in ("Llc", "L.L.C.", "Lp", "L.P.", "Nyc", "Hdfc", "Corp.", "Inc."):
        out = out.replace(acr, acr.upper())
    return out


def _present(row: dict, *keys: str) -> bool:
    return all(row.get(k) is not None for k in keys)


# ── Citywide framing from the score itself ──────────────────────
# Scores are citywide percentiles where higher = better.

def _worse_pct(score: float) -> int:
    return int(round(100 - float(score)))


def _better_pct(score: float) -> int:
    return int(round(float(score)))


# ── Peer-context tail ───────────────────────────────────────────

# Component flags that mean a dimension's standing is driven by ABSENCE of
# data (nothing filed / no track record / unreliable match) rather than
# observed evidence. Peer comparisons against real records read as nonsense
# there ("no filings — among the worst in Williamsburg"), so tails are
# suppressed.
_ABSENCE_FLAGS = (
    "not_required", "never_filed", "new_building", "match_uncertain",
)


def _is_absence_driven(dim: str, row: dict) -> bool:
    return any(row.get(f"{dim}_{flag}") for flag in _ABSENCE_FLAGS)


def _peer_tail(dim: str, peer_context: Optional[dict], neighborhood: str,
               citywide_good: Optional[bool]) -> str:
    """', but mid-pack for Williamsburg' style tail, or ''.

    Wording describes the listing's STANDING among neighborhood
    alternatives (score percentile), stated directionally — never
    ambiguous count language. "among the lowest" is reserved for the
    bottom decile.
    """
    ctx = (peer_context or {}).get(dim)
    if not isinstance(ctx, dict):
        return ""
    p = ctx.get("nbhd_percentile")
    if p is None:
        return ""
    p = float(p)
    nbhd = neighborhood or "this area"
    # Wording must be unambiguous after clauses like "loud by city
    # standards" — "among the lowest" reads as "least loud". Always frame
    # as a ranking of the LISTING among its local alternatives.
    if p >= 80:
        phrase, peer_good = f"among the best in {nbhd} for this", True
    elif p >= 55:
        phrase, peer_good = f"better than most {nbhd} listings", True
    elif p >= 45:
        phrase, peer_good = f"mid-pack for {nbhd}", None
    elif p >= 10:
        phrase, peer_good = f"ranks below most {nbhd} listings here", False
    else:
        phrase, peer_good = f"ranks in {nbhd}'s bottom tier for this", False

    if citywide_good is None:
        return f" — {phrase}"
    if peer_good is None:
        # neutral peer standing contrasts with any strong citywide claim
        return f", but {phrase}" if not citywide_good else f", though {phrase}"
    if citywide_good != peer_good:
        return f", but {phrase}"
    return f", and {phrase}"


# ── Per-dimension fact builders ─────────────────────────────────
# Each returns (fact, citywide_clause_or_None, citywide_good_or_None)
# or None when components are missing (caller falls back to generic).

def _deal(row: dict, score: float):
    price = row.get("price")
    median = row.get("comp_median")
    scope = row.get("comp_scope") or "neighborhood"
    scope_word = {"neighborhood": "neighborhood", "borough": "borough"}.get(scope, "market")
    good = score >= 50
    clause = (f"a better deal than {_better_pct(score)}% of NYC" if good
              else f"pricier than {_worse_pct(score)}% of NYC for what you get")
    if price and median:
        diff = (float(price) - float(median)) / float(median) * 100.0
        if abs(diff) < 2:
            fact = f"Priced right at the {scope_word} median (${_num(median)}) for comparable units"
        elif diff < 0:
            fact = f"Priced {_num(abs(diff))}% below the {scope_word} median of ${_num(median)} for comparable units"
        else:
            fact = f"Priced {_num(diff)}% above the {scope_word} median of ${_num(median)} for comparable units"
        return fact, clause, good
    pps = row.get("price_per_sqft")
    if pps:
        return f"Rents at ${_flt(pps, 2)}/sqft", clause, good
    return None


def _unit_amenities(row: dict, score: float):
    prem = row.get("unit_amenities_premium")
    std = row.get("unit_amenities_standard")
    good = score >= 50
    clause = (f"better equipped than {_better_pct(score)}% of NYC listings" if good
              else f"more sparsely equipped than {_worse_pct(score)}% of NYC listings")
    if prem is None and std is None:
        return None
    if (prem or 0) == 0 and (std or 0) == 0:
        return "No in-unit amenities listed", clause, good
    parts = []
    if prem is not None:
        parts.append(f"{_num(prem)} premium")
    if std is not None:
        parts.append(f"{_num(std)} standard")
    return f"{' and '.join(parts)} in-unit amenities listed", clause, good


def _transit(row: dict, score: float):
    nearest = row.get("transit_nearest_m")
    stations = row.get("transit_station_count")
    routes = row.get("transit_routes_served")
    reach = []
    if stations is not None:
        reach.append(_plural(stations, "station"))
    if routes is not None:
        reach.append(f"{_num(routes)} unique routes" if routes != 1 else "1 route")
    reach_str = " and ".join(reach)
    if nearest is not None and float(nearest) < 9000:
        fact = f"Nearest subway station {_dist(nearest)} away"
        if reach_str:
            fact += f"; {reach_str} within a 10-minute walk"
        return fact, None, score >= 50
    if reach_str:
        return f"{reach_str.capitalize()} within a 10-minute walk", None, score >= 50
    return None


def _crime(row: dict, score: float):
    fel = row.get("crime_felony_count")
    misd = row.get("crime_misdemeanor_count")
    if fel is None and misd is None:
        return None
    good = score >= 50
    clause = (f"lower than {_better_pct(score)}% of NYC" if good
              else f"more than {_worse_pct(score)}% of NYC")
    bits = []
    if fel is not None:
        bits.append(_plural(fel, "felony", "felonies"))
    if misd is not None:
        bits.append(_plural(misd, "misdemeanor"))
    return f"{' and '.join(bits)} within ~4 blocks in the last year", clause, good


def _noise(row: dict, score: float):
    cnt = row.get("noise_complaint_count")
    if cnt is None:
        return None
    good = score >= 50
    if score >= 75:
        clause = "quiet by city standards"
    elif score >= 50:
        clause = "quieter than average for NYC"
    elif score >= 25:
        clause = "noisier than most of NYC"
    else:
        clause = "loud by city standards"
    fact = f"{_plural(cnt, 'noise complaint')} within ~2 blocks last year"
    return fact, clause, good


def _building_violations(row: dict, score: float):
    dob = row.get("building_violation_count")
    class_c = row.get("building_hpd_class_c")
    per_unit = row.get("building_violations_per_unit")
    if dob is None:
        return None
    good = score >= 50
    if int(dob) == 0:
        fact = "No open DOB violations in this building"
        if class_c is not None and int(class_c) > 0:
            fact += f", but {_plural(class_c, 'hazardous HPD Class C violation')}"
            good = False
        return fact, None, good
    fact = f"{_plural(dob, 'open DOB violation')} in this building"
    if per_unit is not None:
        fact += f" ({_flt(per_unit, 2)} per unit)"
    if class_c is not None and int(class_c) > 0:
        fact += f", plus {_plural(class_c, 'hazardous HPD Class C violation')}"
    return fact, None, good


def _parks(row: dict, score: float):
    dist = row.get("parks_distance_m")
    name = row.get("parks_name")
    acres = row.get("parks_acres")
    if dist is None:
        return None
    if name:
        fact = f"{name}"
        if acres is not None:
            fact += f" ({_flt(acres, 1)} acres)"
        fact += f" is {_dist(dist)} away"
    else:
        fact = f"Nearest park {_dist(dist)} away"
    return fact, None, score >= 50


def _schools(row: dict, score: float):
    name = row.get("school_name")
    rating = row.get("school_rating")
    if not name:
        return None
    fact = f"Zoned near {name}"
    if rating is not None:
        fact += f", rated {_num(rating)}/100"
    return fact, None, score >= 50


def _management(row: dict, score: float):
    owner = row.get("mgmt_owner")
    cpu = row.get("mgmt_complaints_per_unit")
    bldgs = row.get("mgmt_owner_buildings")
    units = row.get("mgmt_owner_units")
    lit = row.get("mgmt_litigations")
    evic = row.get("mgmt_evictions")
    good = score >= 50
    clause = ("better than most NYC landlords" if good
              else f"worse than {_worse_pct(score)}% of NYC landlords")
    if cpu is None and owner is None:
        return None
    parts = []
    if owner:
        lead = f"{_title_owner(owner)}'s portfolio"
        if bldgs and units:
            lead += f" ({_plural(bldgs, 'building')}, {_plural(units, 'unit')})"
    else:
        lead = "Landlord portfolio"
    if cpu is not None:
        cpu_f = float(cpu)
        rate = "under 0.01" if 0 < cpu_f < 0.01 else _flt(cpu_f, 2)
        parts.append(f"{lead} averages {rate} HPD complaints per unit")
    else:
        parts.append(f"Building is owned by {_title_owner(owner)}")
    extras = []
    if lit is not None and int(lit) > 0:
        extras.append(_plural(lit, "open HPD litigation"))
    if evic is not None and int(evic) > 0:
        extras.append(_plural(evic, "eviction filing"))
    fact = parts[0]
    if extras:
        fact += f", with {' and '.join(extras)}"
    return fact, clause, good


def _convenience(row: dict, score: float):
    grocery = row.get("convenience_grocery")
    pharmacy = row.get("convenience_pharmacy")
    dining = row.get("convenience_dining")
    gym = row.get("convenience_gym")
    laundry = row.get("convenience_laundry")
    bits = []
    if grocery is not None:
        bits.append(_plural(grocery, "grocery store"))
    if pharmacy is not None and int(pharmacy) > 0:
        bits.append(_plural(pharmacy, "pharmacy", "pharmacies"))
    if dining is not None:
        bits.append(_plural(dining, "restaurant"))
    if gym is not None and int(gym) > 0:
        bits.append(_plural(gym, "gym"))
    if laundry is not None and int(laundry) > 0:
        bits.append(_plural(laundry, "laundromat"))
    if not bits:
        return None
    if len(bits) > 1:
        listed = ", ".join(bits[:-1]) + f", and {bits[-1]}"
    else:
        listed = bits[0]
    return f"{listed} within a short walk", None, score >= 50


def _shelter(row: dict, score: float):
    cnt = row.get("shelter_count")
    nearest = row.get("shelter_nearest_m")
    projects = row.get("project_count")
    if cnt is None and projects is None:
        return None
    parts = []
    if cnt is not None:
        if int(cnt) == 0:
            parts.append("No homeless shelters within a half-mile")
        else:
            p = f"{_plural(cnt, 'homeless shelter')} within a half-mile"
            if nearest is not None and float(nearest) < 9000:
                p += f" (nearest {_dist(nearest)})"
            parts.append(p)
    if projects is not None:
        if int(projects) == 0:
            parts.append("no NYCHA developments nearby")
        else:
            parts.append(f"{_plural(projects, 'NYCHA development')} nearby")
    fact = "; ".join(parts)
    return fact[0].upper() + fact[1:], None, score >= 50


def _pest(row: dict, score: float):
    hpd = row.get("pest_hpd_count")
    rodent = row.get("pest_rodent_count")
    if hpd is None and rodent is None:
        return None
    parts = []
    if hpd is not None:
        if int(hpd) == 0:
            parts.append("No pest complaints in this building")
        else:
            parts.append(f"{_plural(hpd, 'pest complaint')} in this building")
    if rodent is not None:
        if int(rodent) == 0:
            parts.append("no rodent reports on the block last year")
        else:
            parts.append(f"{_plural(rodent, 'rodent report')} on the block last year")
    fact = "; ".join(parts)
    return fact[0].upper() + fact[1:], None, score >= 50


def _greenery(row: dict, score: float):
    trees = row.get("greenery_tree_count")
    gardens = row.get("greenery_garden_count")
    if trees is None:
        return None
    good = score >= 50
    if score >= 75:
        clause = "greener than most of NYC"
    elif score >= 50:
        clause = "more greenery than the city average"
    elif score >= 25:
        clause = "less green than most of NYC"
    else:
        clause = "one of the least green blocks in the city"
    fact = f"{_plural(trees, 'street tree')} within ~2 blocks"
    if gardens is not None and int(gardens) > 0:
        fact += f" and {_plural(gardens, 'community garden')} nearby"
    return fact, clause, good


def _bedbug(row: dict, score: float):
    filings = row.get("bedbug_filings")
    infested = row.get("bedbug_infested_total")
    reinfested = row.get("bedbug_reinfested_total")
    if filings is None or int(filings) == 0:
        return "No bedbug filings on record for this building", None, score >= 50
    if infested is None:
        return f"{_plural(filings, 'annual bedbug filing')} on record for this building", None, score >= 50
    if float(infested) == 0:
        return (f"{_plural(filings, 'annual bedbug filing')} on record, none reporting an infestation",
                None, True)
    fact = f"{_plural(infested, 'bedbug infestation')} reported in this building's filing history"
    if reinfested is not None and float(reinfested) > 0:
        fact += f", including {_plural(reinfested, 're-infestation')}"
    return fact, None, False


def _street_danger(row: dict, score: float):
    inj = row.get("street_danger_injuries")
    deaths = row.get("street_danger_deaths")
    if inj is None and deaths is None:
        return None
    good = score >= 50
    clause = (f"safer streets than {_better_pct(score)}% of NYC" if good
              else f"more dangerous than {_worse_pct(score)}% of NYC")
    if (inj or 0) == 0 and (deaths or 0) == 0:
        return "No pedestrian or cyclist injuries within ~3 blocks in the last year", None, True
    bits = []
    if inj is not None:
        bits.append(_plural(inj, "pedestrian or cyclist injury", "pedestrian and cyclist injuries"))
    if deaths is not None and int(deaths) > 0:
        bits.append(_plural(deaths, "death"))
    return f"{' and '.join(bits)} within ~3 blocks in the last year", clause, good


def _air_quality(row: dict, score: float):
    pm25 = row.get("air_quality_pm25")
    no2 = row.get("air_quality_no2")
    if pm25 is None and no2 is None:
        return None
    good = score >= 50
    clause = (f"cleaner air than {_better_pct(score)}% of NYC" if good
              else f"worse air than {_worse_pct(score)}% of NYC")
    bits = []
    if pm25 is not None:
        bits.append(f"annual average PM2.5 of {_flt(pm25, 1)} µg/m³")
    if no2 is not None:
        bits.append(f"NO2 of {_flt(no2, 1)} ppb")
    fact = " and ".join(bits)
    return fact[0].upper() + fact[1:], clause, good


def _road_exposure(row: dict, score: float):
    hwy_d = row.get("road_exposure_hwy_dist_m")
    shield = int(row.get("road_exposure_hwy_shield_rows") or 0)
    truck = float(row.get("road_exposure_truck") or 0.0)
    el_d = row.get("road_exposure_el_dist_m")
    arterial = float(row.get("road_exposure_arterial") or 0.0)
    good = score >= 50

    bits = []
    if hwy_d is not None and float(hwy_d) <= 500:
        hw = f"A highway passes {_dist(hwy_d)} away"
        if shield >= 1:
            hw += (f", though {shield} row{'s' if shield > 1 else ''} of "
                   "buildings shield this block from it")
        bits.append(hw)
    if el_d is not None and float(el_d) <= 400:
        bits.append(f"an elevated train runs {_dist(el_d)} away")
    if truck >= 1.5:
        # Building-level truth with the unit-level caveat: orientation is
        # the one thing no dataset can see.
        bits.append("it fronts a designated truck route — rear-facing "
                    "units will hear far less")
    elif arterial >= 4.0:
        bits.append("it fronts busy surface streets")

    if not bits:
        fact = "Quiet side-street profile — no highways, elevated trains, or truck routes close by"
    else:
        fact = "; ".join(bits)
        fact = fact[0].upper() + fact[1:]

    clause = (f"quieter street exposure than {_better_pct(score)}% of NYC listings" if good
              else f"more road noise than {_worse_pct(score)}% of NYC listings")
    return fact, clause, good


_BUILDERS = {
    "road_exposure": _road_exposure,
    "deal": _deal,
    "unit_amenities": _unit_amenities,
    "transit": _transit,
    "crime": _crime,
    "noise": _noise,
    "building_violations": _building_violations,
    "parks": _parks,
    "schools": _schools,
    "management": _management,
    "convenience": _convenience,
    "shelter": _shelter,
    "pest": _pest,
    "greenery": _greenery,
    "bedbug": _bedbug,
    "street_danger": _street_danger,
    "air_quality": _air_quality,
}

# Generic-fallback labels when component columns are missing entirely.
_DIM_LABELS = {
    "deal": "value",
    "unit_amenities": "in-unit amenities",
    "transit": "transit access",
    "crime": "low crime",
    "noise": "quiet",
    "building_violations": "building upkeep",
    "parks": "park access",
    "schools": "schools",
    "management": "management quality",
    "convenience": "everyday convenience",
    "shelter": "distance from shelters",
    "pest": "pest history",
    "greenery": "street greenery",
    "bedbug": "bedbug history",
    "street_danger": "street safety",
    "air_quality": "air quality",
}


def score_explanations(row: dict, peer_context: Optional[dict]) -> "dict[str, str]":
    """One plain-English explanation sentence per scored dimension.

    Args:
        row: raw listings DB row as a dict (score + component columns).
        peer_context: get_peer_context() output, or None.

    Returns:
        {dim: sentence} for every SCORE_KEYS dim with a non-null score.
    """
    neighborhood = row.get("neighborhood") or ""
    out: "dict[str, str]" = {}
    for dim in SCORE_KEYS:
        raw_score = row.get(f"{dim}_score")
        if raw_score is None:
            continue
        score = float(raw_score)
        built = None
        builder = _BUILDERS.get(dim)
        if builder is not None:
            try:
                built = builder(row, score)
            except (TypeError, ValueError):
                built = None  # malformed component data — fall back
        if built is None:
            # Generic framing from the score alone — never fabricate facts.
            label = _DIM_LABELS.get(dim, dim.replace("_", " "))
            fact = f"Scores better than {_better_pct(score)}% of NYC listings for {label}"
            clause, good = None, score >= 50
        else:
            fact, clause, good = built
        sentence = fact
        if clause:
            sentence += f" — {clause}"
        # No peer comparison when the standing is driven by absence of
        # data rather than observed evidence — "no filings on record,
        # among the worst in Williamsburg" is incoherent.
        if not _is_absence_driven(dim, row):
            sentence += _peer_tail(dim, peer_context, neighborhood, good if clause else None)
        out[dim] = sentence + "."
    return out
