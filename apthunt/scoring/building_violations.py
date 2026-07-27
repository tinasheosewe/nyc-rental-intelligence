"""
BuildingViolationsScorer — scores listings by active building violations.

Combines two violation sources:

1. **DOB Violations** (``ds_dob_violations``) — Department of Buildings
   violations for code/construction issues.  Looked up by BBL.

2. **HPD Violations** (``ds_hpd_violations``) — Housing Preservation &
   Development violations for habitability issues.  Inspector-confirmed,
   classified by severity:
     Class A = non-hazardous (90 days to fix)
     Class B = hazardous (30 days to fix)
     Class C = **immediately hazardous** (24 hours — lead, no heat, gas …)
     Class I = orders/administrative (vacate orders … registration filings)

3. **DOB Active Permits** (``ds_dob_permits``) — active construction
   permits on the building or nearby.  Surfaced as a breakout component
   (not scored — informational only).

Scoring:
    **Attribution guard** — the listing's nearest PLUTO lot must lie
    within ``BUILDING_MATCH_MAX_M`` (40 m; measured p95 match distance
    is 25 m).  Beyond that the "match" is likely a neighbor's lot, so
    the building signal is unknown: score = None and
    ``building_violations_match_uncertain = 1``.

    **Per-listing attribution** — the nearest lot is resolved per unique
    coordinate pair (lat/lon rounded to 5 dp; listings in the same
    building share coordinates, different buildings never do), NOT per
    geohash cell.  A geohash-7 cell (~150 m) can span several buildings,
    so cell-level dedupe let whichever listing computed the cell first
    "own" it — every other building in the cell inherited a stranger's
    building record.  Building stats are cached by BBL
    (``bbl:<normalized_bbl>``) and the lot resolution by coordinate
    (``lot:<lat5>,<lon5>``), both under cache source ``bv_v6``.

    **New buildings** — a building completed within ~3 years that has
    zero violation records scores None with
    ``building_violations_new_building = 1``: "no track record yet",
    not a fake perfect score.

    **DOB type severity** (replaces flat DOB counts) — 79 % of DOB rows
    are administrative annual-filing boilerplate (boiler/elevator/facade
    filing failures, benchmarking, energy-grade posting …) that says
    nothing about living conditions, while a flat count let 40 missed
    boiler filings swamp one Unsafe Building order.  Each row is
    weighted by its ``violation_type`` code (the token before the first
    ``-``) via ``DOB_TYPE_WEIGHTS`` — ×0.15 administrative filings,
    ×1.5–2 construction/structural/egress/elevator/emergency, ×1.0
    everything else (see the map for the full documented inventory).

    **HPD class I split** (previously ignored, 9.5 % of the table) —
    class 'I' mixes the worst signal in the dataset (vacate orders) with
    the mildest (registration paperwork), so rows are split on
    ``novdescription`` text: vacate/order rows (§27-2142, §27-2089,
    §27-2153 AEP, §27-2091) get base 2.0 × 3 = 6.0 — weighted like
    C-plus; registration filings (§27-2107, §27-2096) get 2.0 × 0.2 =
    0.4; unmatched class-I rows stay at the B-like base 2.0.

    **Trajectory & open-age** (needs the re-downloaded
    ``ds_hpd_violations`` carrying *all* statuses + ``certifieddate``;
    probed once per ``score()`` call and degraded gracefully to the
    current open-only snapshot when absent):
      - ``bviol_opened_12mo`` — violations with an ``inspectiondate``
        in the last 365 days (computable today, undercounts until the
        full-status re-download lands);
      - ``bviol_cured_12mo`` — violations certified corrected in the
        last 365 days (needs ``certifieddate``; 0 until then);
      - trajectory ratio opened/cured (``compute_trend`` semantics)
        folded modestly into the rate: deteriorating ×1.3, recovering
        ×0.8, and neutral unless there are ≥ 4 events of evidence;
      - **open-age boost** — open violations sitting unrepaired > 3
        years weigh ×1.5 (neglect, works against today's snapshot).
    When a ``violationstatus`` column exists, class counts and weights
    only include Open rows, so the re-downloaded full-status table
    won't inflate counts with already-cured violations.

    **Exposure-aware empirical-Bayes rate** (replaces zero-is-perfect;
    absence of records is weak evidence for a 3-unit walk-up and strong
    evidence for a 300-unit tower):
        weighted = DOB-type-weighted + HPD per-row weighted
                   (A×1, B×2, C×5, I split as above, ×1.5 open-age)
        weighted × trajectory factor
        rate     = eb_rate(weighted, unitsres, prior, k=10)
                 = (weighted + k·prior) / (unitsres + k)
    where ``prior`` is the citywide rate from the frozen baseline
    (``baseline_median``).  On the very first pass — before
    ``build_baseline.py`` has ever run — the prior is None and
    ``eb_rate`` falls back to the raw rate; scores converge after one
    baseline cycle.  Scored against the frozen citywide baseline
    distribution (batch-percentile fallback until the first baseline
    build).  Fewer/lighter violations per unit → higher score; zero is
    no longer pinned to 100.  NB: the raw-metric definition changed in
    bv_v6 (type weights, class I, trajectory) — a rebaseline follows.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_median, baseline_scores, eb_rate
from apthunt.scoring.utils import (
    BUILDING_MATCH_MAX_M,
    compute_trend,
    find_nearest_row,
    is_new_building,
    normalize_bbl,
    parse_bbl,
    percentile_scores,
    pluto_units,
)

# Cache source for both the per-coordinate lot resolution ("lot:…" keys)
# and the per-building stats ("bbl:…" keys).  Bumped from "bv_v5":
# bv_v6 changes the raw-metric semantics (DOB type-severity weights,
# HPD class-I split, trajectory + open-age boost).
CACHE_SOURCE = "bv_v6"

# ── DOB violation_type severity map ──────────────────────────────────
#
# ds_dob_violations.violation_type is "CODE-DESCRIPTION…"; the map is
# keyed on CODE (token before the first "-", uppercased).  Inventory
# from the live table (2026-07, ~1.78 M rows; 79 % fall in the ×0.15
# administrative tier):
#
# ×0.15 administrative annual-filing / paperwork boilerplate — late or
# missing *filings*, not observed building defects:
#   LL6291   Local Law 62/91 boiler filing        (598 814 rows — #1)
#   LBLVIO   low-pressure boiler filing           (319 929 — #2)
#   HBLVIO   high-pressure boiler filing
#   B        boiler (filing-driven)
#   BENCH    failure to benchmark energy use
#   EGRADE   failure to post energy grade
#   EARCX    failure to submit EER
#   FISP/FISPNRF  facade program late/no report
#   LL1198/L1198/LL11/98, LL1080/LL10/80  facade filing local laws
#   LL1081/LL10/81, LL16  elevator filing local laws
#   ACC1/ACH1/ACJ1  elevator affirmation-of-correction filings
#   EVCAT1/EVCAT5/VCAT1/JVCAT5/HVCAT5/JVIOS/HVIOS  elevator periodic
#            inspection/test filings
#   RWNRF    retaining-wall no report filed
#   LANDMK/LANDMRK  landmark paperwork
#   LL5      Local Law 5/73 fire-safety filing
#   LL2604/LL2604E/LL2604S  photoluminescent/emergency-power/sprinkler
#            filings
#
# ×1.5–2 construction / structural / egress / elevator-outage /
# emergency — conditions an occupant actually lives with:
#   UB ×2      unsafe buildings
#   COMPBLD ×2 structurally compromised building
#   IMEGNCY/IMD ×2  immediate emergency
#   FISPHAZ ×2 hazardous facade condition
#   EGNCY ×1.8 emergency
#   C ×1.5     construction
#   CS ×1.5    site safety
#   E ×1.5     elevator violation (outage/defect, not a filing)
#   FISPFCS ×1.5  failure to correct hazardous (SWARMP) facade
#   CLOS ×1.5  padlock order
#
# ×1.0 everything else (P plumbing, Z zoning, PA public assembly,
# ES electric signs, CMQ marquee, A SEU, AEUHAZ1 fail-to-certify
# correction of a class-1 hazardous ECB violation, unknown codes).
DOB_TYPE_WEIGHTS = {
    # administrative filing tier
    "LL6291": 0.15, "LBLVIO": 0.15, "HBLVIO": 0.15, "B": 0.15,
    "BENCH": 0.15, "EGRADE": 0.15, "EARCX": 0.15,
    "FISP": 0.15, "FISPNRF": 0.15,
    "LL1198": 0.15, "L1198": 0.15, "LL11/98": 0.15,
    "LL1080": 0.15, "LL10/80": 0.15,
    "LL1081": 0.15, "LL10/81": 0.15, "LL16": 0.15,
    "ACC1": 0.15, "ACH1": 0.15, "ACJ1": 0.15,
    "EVCAT1": 0.15, "EVCAT5": 0.15, "VCAT1": 0.15,
    "JVCAT5": 0.15, "HVCAT5": 0.15, "JVIOS": 0.15, "HVIOS": 0.15,
    "RWNRF": 0.15, "LANDMK": 0.15, "LANDMRK": 0.15,
    "LL5": 0.15, "LL2604": 0.15, "LL2604E": 0.15, "LL2604S": 0.15,
    # severe tier
    "UB": 2.0, "COMPBLD": 2.0, "IMEGNCY": 2.0, "IMD": 2.0,
    "FISPHAZ": 2.0, "EGNCY": 1.8,
    "C": 1.5, "CS": 1.5, "E": 1.5, "FISPFCS": 1.5, "CLOS": 1.5,
}

# HPD per-row class weights (open rows).  Class I uses a B-like base
# of 2.0, then splits by novdescription text (see _hpd_i_multiplier).
HPD_CLASS_WEIGHTS = {"A": 1.0, "B": 2.0, "C": 5.0}
HPD_I_BASE_WEIGHT = 2.0

# Open violations unrepaired for more than this many days weigh ×1.5.
OPEN_AGE_BOOST_DAYS = 3 * 365
OPEN_AGE_BOOST = 1.5

# Trajectory fold: modest, and only with ≥ MIN_EVENTS of evidence.
TRAJECTORY_DETERIORATING = 1.3
TRAJECTORY_RECOVERING = 0.8
TRAJECTORY_MIN_EVENTS = 4


def _dob_type_weight(violation_type) -> float:
    """Severity weight for one DOB row from its violation_type code."""
    code = (violation_type or "").split("-", 1)[0].strip().upper()
    return DOB_TYPE_WEIGHTS.get(code, 1.0)


def _hpd_i_multiplier(novdescription) -> float:
    """Class-I novdescription split.

    Registration paperwork (×0.2): §27-2107 "owner failed to file a
    valid registration statement" is 97 % of class I; §27-2096
    false/incomplete registration.  Checked first — its text never
    contains vacate language, but it does contain section numbers.

    Vacate/order (×3, i.e. base 2.0 → 6.0, weighted like C-plus):
    §27-2142 / §27-2089 premises vacated by the department, §27-2153
    Alternative Enforcement Program selection (distressed building),
    §27-2091 commissioner's / underlying-conditions order.
    """
    text = (novdescription or "").upper()
    if "2107" in text or "2096" in text or "REGISTRATION" in text:
        return 0.2
    if (
        "2142" in text or "2089" in text or "2153" in text
        or "2091" in text or "VACAT" in text
    ):
        return 3.0
    return 1.0


def _coord_key(lat: float, lon: float) -> str:
    """Stable per-building coordinate key: lat/lon rounded to 5 dp
    (~1 m).  Listings in the same building share exact coordinates."""
    return f"{round(lat, 5)},{round(lon, 5)}"


class BuildingViolationsScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "building_violations"

    # Citywide baseline declaration (sampled by scripts/build_baseline.py)
    baseline_component = "building_violations_per_unit"
    baseline_reverse = True          # fewer violations per unit = better
    # Zero records is NOT auto-perfect: absence often means non-reporting,
    # and the EB rate already rewards genuinely clean high-exposure buildings.
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "building_violation_count": "INTEGER",
            "building_hpd_class_a": "INTEGER",
            "building_hpd_class_b": "INTEGER",
            "building_hpd_class_c": "INTEGER",
            "building_hpd_class_i": "INTEGER",
            "building_unitsres": "INTEGER",
            "building_violations_per_unit": "REAL",
            "building_active_permits": "INTEGER",
            "building_violations_match_uncertain": "INTEGER",
            "building_violations_new_building": "INTEGER",
            "bviol_dob_weighted": "REAL",
            "bviol_hpd_weighted": "REAL",
            "bviol_opened_12mo": "INTEGER",
            "bviol_cured_12mo": "INTEGER",
            "bviol_trajectory_ratio": "REAL",
            "bviol_open_3yr": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("pluto", quiet=True)
        self._store.ensure_downloaded("dob_violations", quiet=True)
        self._store.ensure_downloaded("hpd_violations", quiet=True)
        self._store.ensure_downloaded("dob_permits", quiet=True)

        # Probe the HPD schema ONCE per score() call: the trajectory
        # metrics need the re-downloaded table (all statuses +
        # certifieddate) and must degrade gracefully against today's
        # open-only snapshot while that re-download is in flight.
        hpd_schema = self._probe_hpd_schema()

        # Per-listing building attribution: resolve the nearest PLUTO lot
        # per unique coordinate pair, never per geohash cell — a cell can
        # span multiple buildings.
        coord_map: dict[str, tuple[float, float]] = {}
        listing_keys: list[str] = []
        for lst in listings:
            key = _coord_key(lst["lat"], lst["lon"])
            listing_keys.append(key)
            coord_map.setdefault(key, (lst["lat"], lst["lon"]))

        block_stats: dict[str, dict] = {}
        for ckey, (lat, lon) in coord_map.items():
            # Lot resolution cached by coordinate (avoids repeated bbox
            # queries for the same building across runs).
            resolution = self._cache.get(f"lot:{ckey}", CACHE_SOURCE)
            if resolution is None:
                resolution = self._resolve_lot(lat, lon)
                self._cache.put(f"lot:{ckey}", CACHE_SOURCE, resolution)

            if resolution.get("bbl") is None:
                # Attribution guard tripped (no lot within
                # BUILDING_MATCH_MAX_M) or the matched lot's BBL was
                # unusable — the building signal is unknown, not clean.
                block_stats[ckey] = {
                    "building_violation_count": 0,
                    "building_hpd_class_a": 0,
                    "building_hpd_class_b": 0,
                    "building_hpd_class_c": 0,
                    "building_hpd_class_i": 0,
                    "building_unitsres": resolution.get("unitsres", 1),
                    "building_active_permits": 0,
                    "building_violations_match_uncertain": 1,
                    "building_violations_new_building": 0,
                }
                continue

            # Building stats cached by BBL — the building's own identity —
            # so no listing can inherit a neighboring lot's record.
            # Recompute when the cached entry was built against a
            # different HPD schema (the re-download landing mid-cache).
            bbl_key = f"bbl:{resolution['bbl']}"
            stats = self._cache.get(bbl_key, CACHE_SOURCE)
            if stats is None or stats.get("_traj") != int(hpd_schema["trajectory"]):
                stats = self._build_stats(resolution, hpd_schema)
                self._cache.put(bbl_key, CACHE_SOURCE, stats)
            block_stats[ckey] = stats

        # Exposure-aware empirical-Bayes rate per unit: shrinks
        # thin-evidence buildings toward the citywide prior, so a 3-unit
        # building with 0 records reads mid-high (honest uncertainty)
        # while a 300-unit building with 0 records earns a top score.
        # The prior is None until the first build_baseline.py run —
        # eb_rate then falls back to the raw rate, and scores converge
        # after one baseline cycle.
        prior = baseline_median(conn, self.name)

        per_units: list = []  # float | None per listing
        for key in listing_keys:
            s = block_stats[key]
            if (
                s.get("building_violations_match_uncertain")
                or s.get("building_violations_new_building")
            ):
                per_units.append(None)  # building signal unknown
                continue
            weighted = (
                s.get("bviol_dob_weighted", 0.0)
                + s.get("bviol_hpd_weighted", 0.0)
            ) * s.get("bviol_trajectory_factor", 1.0)
            per_units.append(
                eb_rate(weighted, s["building_unitsres"], prior, k=10.0)
            )

        # Absolute scoring against the frozen citywide baseline;
        # batch-relative fallback until the first baseline build.
        # None rates stay None (unknown building signal).
        scores = baseline_scores(conn, self.name, per_units, reverse=True)
        if scores is None:
            known_idx = [i for i, v in enumerate(per_units) if v is not None]
            known_scores = percentile_scores(
                [per_units[i] for i in known_idx], reverse=True,
            )
            scores = [None] * len(per_units)
            for i, sc in zip(known_idx, known_scores):
                scores[i] = sc

        results: list[ScorerResult] = []
        for i, lst in enumerate(listings):
            s = block_stats[listing_keys[i]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=scores[i],
                    components={
                        "building_violation_count": s.get(
                            "building_violation_count", 0
                        ),
                        "building_hpd_class_a": s.get("building_hpd_class_a", 0),
                        "building_hpd_class_b": s.get("building_hpd_class_b", 0),
                        "building_hpd_class_c": s.get("building_hpd_class_c", 0),
                        "building_hpd_class_i": s.get("building_hpd_class_i", 0),
                        "building_unitsres": s.get("building_unitsres", 1),
                        "building_violations_per_unit": (
                            round(per_units[i], 7)
                            if per_units[i] is not None
                            else None
                        ),
                        "building_active_permits": s.get(
                            "building_active_permits", 0
                        ),
                        "building_violations_match_uncertain": s.get(
                            "building_violations_match_uncertain", 0
                        ),
                        "building_violations_new_building": s.get(
                            "building_violations_new_building", 0
                        ),
                        "bviol_dob_weighted": s.get("bviol_dob_weighted"),
                        "bviol_hpd_weighted": s.get("bviol_hpd_weighted"),
                        "bviol_opened_12mo": s.get("bviol_opened_12mo"),
                        "bviol_cured_12mo": s.get("bviol_cured_12mo"),
                        "bviol_trajectory_ratio": s.get("bviol_trajectory_ratio"),
                        "bviol_open_3yr": s.get("bviol_open_3yr"),
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _probe_hpd_schema(self) -> dict:
        """Probe ds_hpd_violations for the re-downloaded trajectory data.

        Returns
        -------
        ``has_certifieddate``: the certifieddate column exists (unlocks
        cured-in-12mo and the trajectory ratio).
        ``has_status``: a violationstatus column exists — class counts
        and weights then filter to Open rows so the full-status
        re-download can't inflate them with already-cured violations.
        ``has_non_open``: at least one non-Open row exists (the other
        re-download marker).
        ``trajectory``: trajectory data considered present.

        Today's snapshot (open-only, no certifieddate) yields
        trajectory=False and every trajectory metric degrades to its
        neutral value — the module must run against today's DB.
        """
        cols: set = set()
        try:
            sample = self._store.query("hpd_violations", limit=1)
            if sample:
                cols = set(sample[0].keys())
        except Exception:
            pass
        has_certifieddate = "certifieddate" in cols
        has_status = "violationstatus" in cols
        has_non_open = False
        if has_status:
            try:
                has_non_open = bool(
                    self._store.query(
                        "hpd_violations",
                        where_clause=(
                            "violationstatus IS NOT NULL "
                            "AND violationstatus <> 'Open'"
                        ),
                        select="violationid",
                        limit=1,
                    )
                )
            except Exception:
                has_non_open = False
        return {
            "has_certifieddate": has_certifieddate,
            "has_status": has_status,
            "has_non_open": has_non_open,
            # Either marker signals the re-download has landed; the
            # cured/ratio math itself additionally needs certifieddate.
            "trajectory": has_certifieddate or has_non_open,
        }

    def _resolve_lot(self, lat: float, lon: float) -> dict:
        """Resolve the nearest PLUTO lot for one coordinate pair.

        Returns a JSON-serializable dict.  ``bbl`` is None when the
        attribution guard trips (no lot within ``BUILDING_MATCH_MAX_M``)
        or the matched lot's BBL is unparseable — in both cases the
        building signal is unknown.
        """
        pluto_rows = self._store.query_bbox("pluto", lat, lon, delta=0.0015)
        nearest = find_nearest_row(
            pluto_rows, lat, lon, max_dist_m=BUILDING_MATCH_MAX_M,
        )
        if nearest is None:
            return {"bbl": None, "unitsres": 1}

        unitsres = pluto_units(nearest)
        bbl_raw = nearest.get("bbl", "")
        try:
            boro, block, lot = parse_bbl(bbl_raw)
        except (ValueError, IndexError):
            return {"bbl": None, "unitsres": unitsres}

        return {
            "bbl": normalize_bbl(bbl_raw),
            "boro": boro,
            "block": block,
            "lot": lot,
            "unitsres": unitsres,
            "yearbuilt": nearest.get("yearbuilt"),
        }

    def _build_stats(self, resolution: dict, hpd_schema: dict) -> dict:
        """Fetch violation/permit records for one resolved building."""
        boro = resolution["boro"]
        block = resolution["block"]
        lot = resolution["lot"]

        # DOB violations: raw count preserved; the scored contribution
        # is the type-severity-weighted sum.
        dob_rows = self._fetch_dob_violations(boro, block, lot)
        dob_count = len(dob_rows)
        dob_weighted = round(
            sum(_dob_type_weight(r.get("violation_type")) for r in dob_rows), 3
        )

        # HPD violations: per-row class/novdescription/age weighting
        # plus trajectory counts.
        hpd = self._fetch_hpd_violations(boro, block, lot, hpd_schema)

        # DOB active permits
        permits = self._fetch_permits(boro, block, lot)

        # Brand-new building with no violation history: "no track
        # record yet", not a perfect score.  (Permits don't count —
        # they're informational, not part of this dimension.)
        total_violations = (
            dob_count + hpd["a"] + hpd["b"] + hpd["c"] + hpd["i"]
        )
        new_building = int(
            is_new_building({"yearbuilt": resolution.get("yearbuilt")})
            and total_violations == 0
        )

        return {
            "building_violation_count": dob_count,
            "building_hpd_class_a": hpd["a"],
            "building_hpd_class_b": hpd["b"],
            "building_hpd_class_c": hpd["c"],
            "building_hpd_class_i": hpd["i"],
            "building_unitsres": resolution["unitsres"],
            "building_active_permits": permits,
            "building_violations_match_uncertain": 0,
            "building_violations_new_building": new_building,
            "bviol_dob_weighted": dob_weighted,
            "bviol_hpd_weighted": hpd["weighted"],
            "bviol_opened_12mo": hpd["opened_12mo"],
            "bviol_cured_12mo": hpd["cured_12mo"],
            "bviol_trajectory_ratio": hpd["trajectory_ratio"],
            "bviol_trajectory_factor": hpd["trajectory_factor"],
            "bviol_open_3yr": hpd["open_3yr"],
            # Schema flag the stats were computed under — a cached entry
            # built pre-re-download is recomputed once trajectory data
            # lands (see score()).
            "_traj": int(hpd_schema["trajectory"]),
        }

    def _fetch_dob_violations(self, boro: str, block: str, lot: str) -> list[dict]:
        # Keyed fast path (falls back to the equivalent SQL:
        # boro=? AND block=? AND lot=?).  DOB stores block/lot
        # zero-padded, exactly as parse_bbl hands them to us.
        return self._store.rows_by_key(
            "dob_violations", "boro,block,lot", (boro, block, lot),
            columns=["violation_type"],
        )

    def _fetch_hpd_violations(
        self, boro: str, block: str, lot: str, hpd_schema: dict,
    ) -> dict:
        """Per-building HPD metrics.

        Returns a dict with:
          a/b/c/i          open-row counts per class
          weighted         per-row weighted sum over OPEN rows:
                           class weight (A 1, B 2, C 5; I 2.0 split ×3
                           vacate / ×0.2 registration) × open-age boost
                           (×1.5 when open > 3 yr — neglect)
          opened_12mo      rows inspected in the last 365 days
          cured_12mo       rows certified corrected in the last 365 days
                           (0 until certifieddate exists)
          open_3yr         open rows older than 3 years
          trajectory_ratio opened/cured trend ratio (None until the
                           re-downloaded table lands)
          trajectory_factor 1.3 deteriorating / 0.8 recovering / 1.0
        """
        # HPD violations store block/lot WITHOUT leading zeros,
        # but parse_bbl returns zero-padded values — strip them.
        # Keyed fast path; the column list is derived from the schema
        # probe so a pre-re-download snapshot (no certifieddate /
        # violationstatus) degrades exactly as the old SELECT * did.
        hpd_cols = ["class", "inspectiondate", "novdescription"]
        if hpd_schema["has_certifieddate"]:
            hpd_cols.append("certifieddate")
        if hpd_schema["has_status"]:
            hpd_cols.append("violationstatus")
        rows = self._store.rows_by_key(
            "hpd_violations", "boroid,block,lot",
            (boro, str(int(block)), str(int(lot))),
            columns=hpd_cols,
        )

        today = date.today()
        cutoff_12mo = (today - timedelta(days=365)).isoformat()
        cutoff_3yr = (today - timedelta(days=OPEN_AGE_BOOST_DAYS)).isoformat()

        counts = {"A": 0, "B": 0, "C": 0, "I": 0}
        weighted = 0.0
        opened_12mo = 0
        cured_12mo = 0
        open_3yr = 0

        for r in rows:
            insp = (r.get("inspectiondate") or "")[:10]
            if insp and insp >= cutoff_12mo:
                opened_12mo += 1
            if hpd_schema["has_certifieddate"]:
                cert = (r.get("certifieddate") or "")[:10]
                if cert and cert >= cutoff_12mo:
                    cured_12mo += 1

            # Open determination: use violationstatus when present;
            # a missing/blank status in an open-only snapshot is open.
            status = (r.get("violationstatus") or "").strip().upper()
            is_open = status.startswith("OPEN") if status else True
            if not is_open:
                continue

            cls = (r.get("class") or "").strip().upper()
            if cls not in counts:
                continue
            counts[cls] += 1

            if cls == "I":
                w = HPD_I_BASE_WEIGHT * _hpd_i_multiplier(
                    r.get("novdescription")
                )
            else:
                w = HPD_CLASS_WEIGHTS[cls]

            if insp and insp <= cutoff_3yr:
                open_3yr += 1
                w *= OPEN_AGE_BOOST
            weighted += w

        trajectory_ratio = None
        trajectory_factor = 1.0
        if hpd_schema["has_certifieddate"]:
            trajectory_ratio, direction = compute_trend(
                float(opened_12mo), float(cured_12mo)
            )
            if opened_12mo + cured_12mo >= TRAJECTORY_MIN_EVENTS:
                if direction == "worsening":
                    trajectory_factor = TRAJECTORY_DETERIORATING
                elif direction == "improving":
                    trajectory_factor = TRAJECTORY_RECOVERING

        return {
            "a": counts["A"],
            "b": counts["B"],
            "c": counts["C"],
            "i": counts["I"],
            "weighted": round(weighted, 3),
            "opened_12mo": opened_12mo,
            "cured_12mo": cured_12mo,
            "open_3yr": open_3yr,
            "trajectory_ratio": trajectory_ratio,
            "trajectory_factor": trajectory_factor,
        }

    def _fetch_permits(self, boro: str, block: str, lot: str) -> int:
        """Count active DOB permits for this BBL."""
        # DOB permits store borough as name, we have numeric boro code
        _BORO_MAP = {
            "1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN",
            "4": "QUEENS", "5": "STATEN ISLAND",
        }
        boro_name = _BORO_MAP.get(boro, "")
        if not boro_name:
            return 0
        # Keyed fast path — COUNT(*) over an exact key is just len()
        # of the key's row list (columns=[] → key columns only).
        return len(self._store.rows_by_key(
            "dob_permits", "borough,block,lot",
            (boro_name, block, lot), columns=[],
        ))
