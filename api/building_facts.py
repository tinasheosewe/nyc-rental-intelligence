"""
Building forensics — request-time facts for the paid building report.

NOT a scorer.  This is API-layer code: given a listing's (lat, lon) it
resolves the nearest PLUTO lot (BBL — ds_pluto has no BIN; a BIN is
recovered opportunistically from the HPD registration row) and returns
an openigloo-superset dict of hard facts pulled straight from the local
``ds_*`` dataset tables:

    vacate_orders   — HPD vacate orders (active = not rescinded)
    aep             — Alternative Enforcement Program status
    bedbugs         — annual owner bedbug filings
    speculation     — Speculation Watch List (flipped / likely-speculator buy)
    tax_lien        — DOF tax-lien-sale cycles (financial distress)
    sidewalk_shed   — active shed + shed age in days
    elevators       — active DOB devices
    ecb             — open OATH/ECB violations + balance due
    omo             — HPD emergency-repair orders billed to the owner (2 yr)
    registration    — current HPD registration + head officer / corporation

Every dataset may still be mid-download, so every query is wrapped:
a missing table yields ``None`` for that topic key rather than a crash.

Conventions follow api/neighborhood.py: the module takes an open sqlite3
connection (callers get it from apthunt.db get_connection).  Pure stdlib.
"""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import date, datetime
from typing import Any, Optional


# ── Parse-safe primitives ──────────────────────────────────────

def _to_float(val: Any) -> Optional[float]:
    """Parse a float or return None (never raises)."""
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _to_int(val: Any) -> Optional[int]:
    """Parse an int (accepting '12.0' style strings) or return None."""
    f = _to_float(val)
    return int(f) if f is not None else None


def _iso_date(val: Any) -> Optional[str]:
    """Normalise a date-ish value to 'YYYY-MM-DD' (or None).

    Handles ISO timestamps ('2024-03-01T00:00:00'), plain ISO dates,
    BIS-era 'YYYYMMDD' strings, and 'MM/DD/YYYY'.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    # ISO date / timestamp
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return "%s-%s-%s" % m.groups()
    # BIS YYYYMMDD
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        return "%s-%s-%s" % m.groups()
    # US MM/DD/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return "%s-%02d-%02d" % (m.group(3), int(m.group(1)), int(m.group(2)))
    return None


def _days_since(iso: Optional[str]) -> Optional[int]:
    """Days elapsed from an ISO 'YYYY-MM-DD' date to today."""
    if not iso:
        return None
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        return (date.today() - d).days
    except ValueError:
        return None


def _text(val: Any) -> Optional[str]:
    """Strip a text value; empty → None."""
    if val is None:
        return None
    s = str(val).strip()
    return s or None


def _normalize_bbl(raw: Any) -> Optional[str]:
    """'1234567890.00000000' → '1234567890' (plain integer string)."""
    try:
        return str(int(float(raw)))
    except (TypeError, ValueError):
        s = _text(raw)
        return s


def _bbl_variants(bbl: str) -> tuple:
    """Textual forms a BBL may take across ds_* tables."""
    return (bbl, bbl + ".00000000", bbl + ".0")


def normalize_name(raw: Any) -> str:
    """Uppercase, strip punctuation, collapse whitespace.

    Punctuation is removed (not turned into spaces) so common corporate
    variants collapse: ``"Acme Realty, L.L.C."`` == ``"ACME REALTY LLC"``.
    """
    if raw is None:
        return ""
    s = re.sub(r"[^A-Z0-9 ]+", "", str(raw).upper())
    return re.sub(r"\s+", " ", s).strip()


# ── Safe SQL access ────────────────────────────────────────────

def _safe_rows(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple = (),
) -> Optional[list]:
    """Run a query; a missing / mid-download table returns None."""
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return None


def _bbl_where(column: str, bbl: str) -> tuple:
    """(where_fragment, params) matching any textual form of the BBL."""
    variants = _bbl_variants(bbl)
    frag = "%s IN (%s)" % (column, ", ".join("?" for _ in variants))
    return frag, variants


# ── PLUTO lot resolution ───────────────────────────────────────

def _resolve_lot(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
    delta: float = 0.0015,
) -> Optional[dict]:
    """Nearest PLUTO lot to (lat, lon) within a small bbox."""
    rows = _safe_rows(
        conn,
        "SELECT bbl, address, unitsres, yearbuilt, numfloors, ownername, "
        "latitude, longitude FROM ds_pluto "
        "WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?",
        (lat - delta, lat + delta, lon - delta, lon + delta),
    )
    if not rows:
        return None
    best, best_d = None, float("inf")
    for row in rows:
        rlat = _to_float(row.get("latitude"))
        rlon = _to_float(row.get("longitude"))
        if rlat is None or rlon is None:
            continue
        # Equirectangular approx is fine at bbox scale (~150 m)
        d = (rlat - lat) ** 2 + ((rlon - lon) * 0.7635) ** 2
        if d < best_d:
            best, best_d = row, d
    return best


# ── Per-topic fact extractors ──────────────────────────────────
# Each returns a sub-dict, or None when the backing table is missing.

def _vacate_facts(conn: sqlite3.Connection, bbl: str) -> Optional[dict]:
    frag, params = _bbl_where("bbl", bbl)
    rows = _safe_rows(
        conn,
        "SELECT primary_vacate_reason, vacate_type, vacate_effective_date, "
        "actual_rescind_date, number_of_vacated_units "
        "FROM ds_vacate_orders WHERE " + frag,
        params,
    )
    if rows is None:
        return None
    active = [
        r for r in rows
        if _text(r.get("actual_rescind_date")) is None
    ]
    latest = None
    if active:
        latest = max(
            active,
            key=lambda r: _iso_date(r.get("vacate_effective_date")) or "",
        )
    return {
        "has_active_vacate_order": bool(active),
        "active_count": len(active),
        "total_orders": len(rows),
        "reason": _text(latest.get("primary_vacate_reason")) if latest else None,
        "vacate_type": _text(latest.get("vacate_type")) if latest else None,
        "effective_date": _iso_date(latest.get("vacate_effective_date")) if latest else None,
        "units_vacated": _to_int(latest.get("number_of_vacated_units")) if latest else None,
    }


def _aep_facts(conn: sqlite3.Connection, bbl: str) -> Optional[dict]:
    frag, params = _bbl_where("bbl", bbl)
    rows = _safe_rows(
        conn,
        "SELECT current_status, aep_round, of_b_c_violations_at_start, "
        "aep_start_date, discharge_date FROM ds_aep_buildings WHERE " + frag,
        params,
    )
    if rows is None:
        return None
    if not rows:
        return {"in_aep": False, "current_status": None, "aep_round": None,
                "violations_at_start": None, "start_date": None,
                "discharge_date": None}
    latest = max(rows, key=lambda r: _iso_date(r.get("aep_start_date")) or "")
    status = _text(latest.get("current_status"))
    return {
        "in_aep": not (status or "").upper().startswith("DISCHARGE"),
        "current_status": status,
        "aep_round": _text(latest.get("aep_round")),
        "violations_at_start": _to_int(latest.get("of_b_c_violations_at_start")),
        "start_date": _iso_date(latest.get("aep_start_date")),
        "discharge_date": _iso_date(latest.get("discharge_date")),
    }


def _bedbug_facts(conn: sqlite3.Connection, bbl: str) -> Optional[dict]:
    frag, params = _bbl_where("bbl", bbl)
    rows = _safe_rows(
        conn,
        "SELECT filing_date, infested_dwelling_unit_count, "
        "re_infested_dwelling_unit, eradicated_unit_count "
        "FROM ds_bedbug_reporting WHERE " + frag,
        params,
    )
    if rows is None:
        return None
    total_infested = 0
    total_reinfested = 0
    last_infested: Optional[str] = None
    for r in rows:
        n = _to_int(r.get("infested_dwelling_unit_count")) or 0
        total_infested += n
        total_reinfested += _to_int(r.get("re_infested_dwelling_unit")) or 0
        if n > 0:
            d = _iso_date(r.get("filing_date"))
            if d and (last_infested is None or d > last_infested):
                last_infested = d
    return {
        "filings": len(rows),
        "total_infested_units": total_infested,
        "total_reinfested_units": total_reinfested,
        "last_infested_filing_date": last_infested,
    }


def _speculation_facts(conn: sqlite3.Connection, bbl: str) -> Optional[dict]:
    frag, params = _bbl_where("bbl", bbl)
    rows = _safe_rows(
        conn,
        "SELECT grantee, deed_date, price FROM ds_speculation_watch_list "
        "WHERE " + frag,
        params,
    )
    if rows is None:
        return None
    if not rows:
        return {"on_watch_list": False, "grantee": None,
                "deed_date": None, "price": None}
    latest = max(rows, key=lambda r: _iso_date(r.get("deed_date")) or "")
    return {
        "on_watch_list": True,
        "grantee": _text(latest.get("grantee")),
        "deed_date": _iso_date(latest.get("deed_date")),
        "price": _to_float(latest.get("price")),
    }


_TRUTHY = {"YES", "Y", "TRUE", "1"}


def _tax_lien_facts(conn: sqlite3.Connection, bbl: str) -> Optional[dict]:
    frag, params = _bbl_where("bbl", bbl)
    rows = _safe_rows(
        conn,
        "SELECT month, cycle, water_debt_only FROM ds_tax_lien_sale "
        "WHERE " + frag,
        params,
    )
    if rows is None:
        return None
    entries = []
    for r in sorted(rows, key=lambda r: _iso_date(r.get("month")) or "",
                    reverse=True):
        entries.append({
            "month": _iso_date(r.get("month")) or _text(r.get("month")),
            "cycle": _text(r.get("cycle")),
            "water_debt_only": (_text(r.get("water_debt_only")) or "").upper()
            in _TRUTHY,
        })
    return {
        "recent_entries": entries,
        "entry_count": len(entries),
        "all_water_debt_only": bool(entries)
        and all(e["water_debt_only"] for e in entries),
    }


def _shed_facts(
    conn: sqlite3.Connection,
    bbl: str,
    bin_num: Optional[str],
) -> Optional[dict]:
    frag, params = _bbl_where("bbl", bbl)
    if bin_num:
        frag = "(%s OR bin = ?)" % frag
        params = params + (bin_num,)
    rows = _safe_rows(
        conn,
        "SELECT permit_status, approved_date, issued_date, expired_date "
        "FROM ds_sidewalk_sheds WHERE " + frag,
        params,
    )
    if rows is None:
        return None
    today = date.today().isoformat()
    active_rows = []
    earliest_approved: Optional[str] = None
    for r in rows:
        approved = _iso_date(r.get("approved_date"))
        if approved and (earliest_approved is None or approved < earliest_approved):
            earliest_approved = approved
        expired = _iso_date(r.get("expired_date"))
        status = (_text(r.get("permit_status")) or "").upper()
        if status in ("REVOKED", "WITHDRAWN", "SUPERSEDED"):
            continue
        if expired is None or expired >= today:
            active_rows.append(r)
    latest_active = None
    if active_rows:
        latest_active = max(
            active_rows, key=lambda r: _iso_date(r.get("issued_date")) or "",
        )
    return {
        "has_active_shed": bool(active_rows),
        "permit_status": _text(latest_active.get("permit_status")) if latest_active else None,
        "expired_date": _iso_date(latest_active.get("expired_date")) if latest_active else None,
        "first_approved_date": earliest_approved if active_rows else None,
        "shed_age_days": _days_since(earliest_approved) if active_rows else None,
        "permit_count": len(rows),
    }


def _elevator_facts(conn: sqlite3.Connection, bbl: str) -> Optional[dict]:
    frag, params = _bbl_where("bbl", bbl)
    rows = _safe_rows(
        conn,
        "SELECT device_number, device_type, device_status "
        "FROM ds_elevator_compliance WHERE " + frag,
        params,
    )
    if rows is None:
        return None
    # Table is downloaded with device_status='Active', but re-check anyway.
    active = [
        r for r in rows
        if (_text(r.get("device_status")) or "Active").upper() == "ACTIVE"
    ]
    types = sorted({
        t for t in (_text(r.get("device_type")) for r in active) if t
    })
    return {
        "active_devices": len({
            _text(r.get("device_number")) or id(r) for r in active
        }),
        "device_types": types,
    }


def _ecb_facts(
    conn: sqlite3.Connection,
    bbl: str,
    bin_num: Optional[str],
) -> Optional[dict]:
    # ECB is keyed by BIN; the table also carries a derived BBL column.
    if bin_num:
        frag, params = "bin = ?", (bin_num,)
    else:
        frag, params = _bbl_where("bbl", bbl)
    rows = _safe_rows(
        conn,
        "SELECT ecb_violation_status, balance_due, severity "
        "FROM ds_ecb_violations WHERE " + frag,
        params,
    )
    if rows is None:
        return None
    open_rows = [
        r for r in rows
        if "RESOLVE" not in (_text(r.get("ecb_violation_status")) or "").upper()
    ]
    balance = sum(_to_float(r.get("balance_due")) or 0.0 for r in open_rows)
    return {
        "open_count": len(open_rows),
        "total_count": len(rows),
        "total_balance_due": round(balance, 2),
    }


def _omo_facts(conn: sqlite3.Connection, bbl: str) -> Optional[dict]:
    frag, params = _bbl_where("bbl", bbl)
    rows = _safe_rows(
        conn,
        "SELECT omoawardamount, omocreatedate FROM ds_omo_charges "
        "WHERE " + frag,
        params,
    )
    if rows is None:
        return None
    total = sum(_to_float(r.get("omoawardamount")) or 0.0 for r in rows)
    return {
        # dataset already windowed to 2 years at download time
        "order_count": len(rows),
        "total_awarded": round(total, 2),
    }


def _registration_facts(conn: sqlite3.Connection, bbl: str) -> Optional[dict]:
    frag, params = _bbl_where("bbl", bbl)
    regs = _safe_rows(
        conn,
        "SELECT registrationid, bin, lastregistrationdate, registrationenddate "
        "FROM ds_hpd_registrations WHERE " + frag,
        params,
    )
    if regs is None:
        return None
    if not regs:
        return {"registered": False, "registrationid": None, "bin": None,
                "expired": None, "last_registration_date": None,
                "registration_end_date": None, "head_officer": None,
                "corporation": None, "business_address": None}
    latest = max(
        regs, key=lambda r: _iso_date(r.get("lastregistrationdate")) or "",
    )
    end = _iso_date(latest.get("registrationenddate"))
    expired = bool(end and end < date.today().isoformat())
    reg_id = _text(latest.get("registrationid"))

    head_officer = corporation = business_address = None
    contacts = _contacts_for_registration(conn, reg_id) if reg_id else []
    if contacts:
        officer = _pick_contact(contacts, ("HEADOFFICER",))
        corp = _pick_contact(contacts, ("CORPORATEOWNER",))
        if officer:
            head_officer = _contact_fullname(officer)
        corporation = _text((corp or {}).get("corporationname")) or next(
            (t for t in (_text(c.get("corporationname")) for c in contacts) if t),
            None,
        )
        addr_row = officer or corp or contacts[0]
        business_address = _contact_address(addr_row)

    return {
        "registered": True,
        "registrationid": reg_id,
        "bin": _text(latest.get("bin")),
        "expired": expired,
        "last_registration_date": _iso_date(latest.get("lastregistrationdate")),
        "registration_end_date": end,
        "head_officer": head_officer,
        "corporation": corporation,
        "business_address": business_address,
    }


# ── HPD contact helpers ────────────────────────────────────────

_CONTACT_COLS = (
    "registrationcontactid, registrationid, type, corporationname, "
    "firstname, lastname, businesshousenumber, businessstreetname, "
    "businessapartment, businesscity, businessstate, businesszip"
)


def _contacts_for_registration(
    conn: sqlite3.Connection,
    reg_id: str,
) -> list:
    rows = _safe_rows(
        conn,
        "SELECT %s FROM ds_hpd_registration_contacts "
        "WHERE registrationid = ?" % _CONTACT_COLS,
        (reg_id,),
    )
    return rows or []


def _pick_contact(contacts: list, types: tuple) -> Optional[dict]:
    for c in contacts:
        if normalize_name(c.get("type")).replace(" ", "") in types:
            return c
    return None


def _contact_fullname(contact: dict) -> Optional[str]:
    parts = [_text(contact.get("firstname")), _text(contact.get("lastname"))]
    name = " ".join(p for p in parts if p)
    return name or None


def _contact_address(contact: dict) -> Optional[str]:
    parts = [
        _text(contact.get("businesshousenumber")),
        _text(contact.get("businessstreetname")),
        _text(contact.get("businessapartment")),
        _text(contact.get("businesscity")),
        _text(contact.get("businessstate")),
        _text(contact.get("businesszip")),
    ]
    addr = " ".join(p for p in parts if p)
    return addr or None


# ── Public API ─────────────────────────────────────────────────

def get_building_facts(
    conn: sqlite3.Connection,
    lat: float,
    lon: float,
) -> dict:
    """Building forensics for the nearest PLUTO lot to (lat, lon).

    Returns a dict with resolved ``bbl`` / ``address`` / ``bin`` plus one
    sub-dict per topic.  A topic whose backing ``ds_*`` table is missing
    (still downloading) is ``None``; a topic with no rows for this
    building is a sub-dict of zero/False facts.
    """
    lot = _resolve_lot(conn, lat, lon)
    bbl = _normalize_bbl(lot.get("bbl")) if lot else None

    facts: dict = {
        "bbl": bbl,
        "address": _text(lot.get("address")) if lot else None,
        "bin": None,
        "units": _to_int(lot.get("unitsres")) if lot else None,
        "year_built": _to_int(lot.get("yearbuilt")) if lot else None,
        "vacate_orders": None,
        "aep": None,
        "bedbugs": None,
        "speculation": None,
        "tax_lien": None,
        "sidewalk_shed": None,
        "elevators": None,
        "ecb": None,
        "omo": None,
        "registration": None,
    }
    if bbl is None:
        return facts

    # Registration first — it recovers the BIN used by shed/ECB lookups.
    registration = _registration_facts(conn, bbl)
    bin_num = registration.get("bin") if registration else None

    facts.update({
        "bin": bin_num,
        "vacate_orders": _vacate_facts(conn, bbl),
        "aep": _aep_facts(conn, bbl),
        "bedbugs": _bedbug_facts(conn, bbl),
        "speculation": _speculation_facts(conn, bbl),
        "tax_lien": _tax_lien_facts(conn, bbl),
        "sidewalk_shed": _shed_facts(conn, bbl, bin_num),
        "elevators": _elevator_facts(conn, bbl),
        "ecb": _ecb_facts(conn, bbl, bin_num),
        "omo": _omo_facts(conn, bbl),
        "registration": registration,
    })
    return facts


def get_landlord_portfolio(
    conn: sqlite3.Connection,
    registrationid: str,
) -> dict:
    """Portfolio stats for the landlord behind an HPD registration.

    Matches other registrations whose contacts share the same normalized
    identity — (corporation name OR head-officer full name) AND business
    address — then aggregates buildings, PLUTO units, and open HPD
    violations across the portfolio.
    """
    out = {
        "building_count": None,
        "total_units": None,
        "portfolio_open_hpd_violations": None,
        "matched_registrations": None,
    }

    own = _contacts_for_registration(conn, str(registrationid))
    if not own:
        return out

    officer = _pick_contact(own, ("HEADOFFICER",))
    corp = _pick_contact(own, ("CORPORATEOWNER",))
    corp_key = normalize_name((corp or {}).get("corporationname")) or next(
        (k for k in (normalize_name(c.get("corporationname")) for c in own) if k),
        "",
    )
    officer_key = normalize_name(_contact_fullname(officer or {}) or "")
    addr_key = normalize_name(
        _contact_address(officer or corp or own[0]) or ""
    )
    if not addr_key or not (corp_key or officer_key):
        return out

    # Candidate prefilter in SQL (bounded scan), exact match in Python.
    clauses, params = [], []
    if corp_key:
        clauses.append("corporationname IS NOT NULL AND corporationname != ''")
    if officer_key and officer:
        clauses.append("UPPER(lastname) = ?")
        params.append(normalize_name(officer.get("lastname")))
    where = " OR ".join("(%s)" % c for c in clauses)
    # Business zip is part of the address key — use it to bound the scan.
    addr_src = officer or corp or own[0]
    zip_raw = _text(addr_src.get("businesszip"))
    if zip_raw:
        where = "(%s) AND TRIM(UPPER(businesszip)) = ?" % where
        params.append(zip_raw.upper())
    candidates = _safe_rows(
        conn,
        "SELECT %s FROM ds_hpd_registration_contacts WHERE %s"
        % (_CONTACT_COLS, where),
        tuple(params),
    )
    if candidates is None:
        return out

    reg_ids = {str(registrationid)}
    for c in candidates:
        if normalize_name(_contact_address(c) or "") != addr_key:
            continue
        name_hit = (
            (corp_key and normalize_name(c.get("corporationname")) == corp_key)
            or (officer_key
                and normalize_name(_contact_fullname(c) or "") == officer_key)
        )
        if not name_hit:
            continue
        rid = _text(c.get("registrationid"))
        if rid:
            reg_ids.add(rid)

    # Registrations → building BBLs
    placeholders = ", ".join("?" for _ in reg_ids)
    regs = _safe_rows(
        conn,
        "SELECT registrationid, bbl FROM ds_hpd_registrations "
        "WHERE registrationid IN (%s)" % placeholders,
        tuple(reg_ids),
    )
    if regs is None:
        return out
    bbls = sorted({
        b for b in (_normalize_bbl(r.get("bbl")) for r in regs) if b
    })
    out["matched_registrations"] = len(reg_ids)
    out["building_count"] = len(bbls)
    if not bbls:
        out["total_units"] = 0
        out["portfolio_open_hpd_violations"] = 0
        return out

    # Units via PLUTO (bbl stored as text, sometimes float-formatted)
    bbl_ints = [int(b) for b in bbls if b.isdigit()]
    if bbl_ints:
        ph = ", ".join("?" for _ in bbl_ints)
        rows = _safe_rows(
            conn,
            "SELECT SUM(CAST(unitsres AS REAL)) AS units FROM ds_pluto "
            "WHERE CAST(CAST(bbl AS REAL) AS INTEGER) IN (%s)" % ph,
            tuple(bbl_ints),
        )
        if rows and rows[0].get("units") is not None:
            out["total_units"] = _to_int(rows[0]["units"])

    # Open HPD violations — table has boroid/block/lot, no bbl column
    ph = ", ".join("?" for _ in bbls)
    rows = _safe_rows(
        conn,
        "SELECT COUNT(*) AS cnt FROM ds_hpd_violations "
        "WHERE UPPER(violationstatus) = 'OPEN' AND "
        "(CAST(CAST(boroid AS INTEGER) AS TEXT) || "
        "printf('%05d', CAST(block AS INTEGER)) || "
        "printf('%04d', CAST(lot AS INTEGER))) IN (" + ph + ")",
        tuple(bbls),
    )
    if rows:
        out["portfolio_open_hpd_violations"] = _to_int(rows[0].get("cnt")) or 0
    return out
