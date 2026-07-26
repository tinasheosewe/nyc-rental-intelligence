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
    (``lot:<lat5>,<lon5>``), both under cache source ``bv_v5``.

    **New buildings** — a building completed within ~3 years that has
    zero violation records scores None with
    ``building_violations_new_building = 1``: "no track record yet",
    not a fake perfect score.

    **Exposure-aware empirical-Bayes rate** (replaces zero-is-perfect;
    absence of records is weak evidence for a 3-unit walk-up and strong
    evidence for a 300-unit tower):
        weighted = DOB + HPD-A×1 + HPD-B×2 + HPD-C×5
        rate     = eb_rate(weighted, unitsres, prior, k=10)
                 = (weighted + k·prior) / (unitsres + k)
    where ``prior`` is the citywide mean rate from the frozen baseline
    (``baseline_median``).  On the very first pass — before
    ``build_baseline.py`` has ever run — the prior is None and
    ``eb_rate`` falls back to the raw rate; scores converge after one
    baseline cycle.  Scored against the frozen citywide baseline
    distribution (batch-percentile fallback until the first baseline
    build).  Fewer violations per unit → higher score; zero is no
    longer pinned to 100.
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_median, baseline_scores, eb_rate
from apthunt.scoring.utils import (
    BUILDING_MATCH_MAX_M,
    find_nearest_row,
    is_new_building,
    normalize_bbl,
    parse_bbl,
    percentile_scores,
    pluto_units,
)

# Cache source for both the per-coordinate lot resolution ("lot:…" keys)
# and the per-building stats ("bbl:…" keys).  Bumped from "bv_v4", whose
# entries were keyed by geohash-7 cell and could carry a *neighboring*
# building's record.
CACHE_SOURCE = "bv_v5"


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
            "building_unitsres": "INTEGER",
            "building_violations_per_unit": "REAL",
            "building_active_permits": "INTEGER",
            "building_violations_match_uncertain": "INTEGER",
            "building_violations_new_building": "INTEGER",
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
                    "building_unitsres": resolution.get("unitsres", 1),
                    "building_active_permits": 0,
                    "building_violations_match_uncertain": 1,
                    "building_violations_new_building": 0,
                }
                continue

            # Building stats cached by BBL — the building's own identity —
            # so no listing can inherit a neighboring lot's record.
            bbl_key = f"bbl:{resolution['bbl']}"
            stats = self._cache.get(bbl_key, CACHE_SOURCE)
            if stats is None:
                stats = self._build_stats(resolution)
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
                s["building_violations_match_uncertain"]
                or s["building_violations_new_building"]
            ):
                per_units.append(None)  # building signal unknown
                continue
            weighted = (
                s["building_violation_count"]
                + s["building_hpd_class_a"]
                + s["building_hpd_class_b"] * 2
                + s["building_hpd_class_c"] * 5
            )
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
                        "building_violation_count": s["building_violation_count"],
                        "building_hpd_class_a": s["building_hpd_class_a"],
                        "building_hpd_class_b": s["building_hpd_class_b"],
                        "building_hpd_class_c": s["building_hpd_class_c"],
                        "building_unitsres": s["building_unitsres"],
                        "building_violations_per_unit": (
                            round(per_units[i], 4)
                            if per_units[i] is not None
                            else None
                        ),
                        "building_active_permits": s["building_active_permits"],
                        "building_violations_match_uncertain": s[
                            "building_violations_match_uncertain"
                        ],
                        "building_violations_new_building": s[
                            "building_violations_new_building"
                        ],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

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

    def _build_stats(self, resolution: dict) -> dict:
        """Fetch violation/permit records for one resolved building."""
        boro = resolution["boro"]
        block = resolution["block"]
        lot = resolution["lot"]

        # DOB violations
        dob_count = len(self._fetch_dob_violations(boro, block, lot))

        # HPD violations by class
        hpd_a, hpd_b, hpd_c = self._fetch_hpd_violations(boro, block, lot)

        # DOB active permits
        permits = self._fetch_permits(boro, block, lot)

        # Brand-new building with no violation history: "no track
        # record yet", not a perfect score.  (Permits don't count —
        # they're informational, not part of this dimension.)
        total_violations = dob_count + hpd_a + hpd_b + hpd_c
        new_building = int(
            is_new_building({"yearbuilt": resolution.get("yearbuilt")})
            and total_violations == 0
        )

        return {
            "building_violation_count": dob_count,
            "building_hpd_class_a": hpd_a,
            "building_hpd_class_b": hpd_b,
            "building_hpd_class_c": hpd_c,
            "building_unitsres": resolution["unitsres"],
            "building_active_permits": permits,
            "building_violations_match_uncertain": 0,
            "building_violations_new_building": new_building,
        }

    def _fetch_dob_violations(self, boro: str, block: str, lot: str) -> list[dict]:
        return self._store.query(
            "dob_violations",
            where_clause="boro=? AND block=? AND lot=?",
            params=(boro, block, lot),
            select="isn_dob_bis_viol",
        )

    def _fetch_hpd_violations(
        self, boro: str, block: str, lot: str,
    ) -> tuple[int, int, int]:
        """Return (class_a, class_b, class_c) counts for open HPD violations."""
        # HPD violations store block/lot WITHOUT leading zeros,
        # but parse_bbl returns zero-padded values — strip them.
        rows = self._store.query(
            "hpd_violations",
            where_clause="boroid=? AND block=? AND lot=?",
            params=(boro, str(int(block)), str(int(lot))),
            select="class",
        )
        a = b = c = 0
        for r in rows:
            cls = (r.get("class") or "").upper()
            if cls == "A":
                a += 1
            elif cls == "B":
                b += 1
            elif cls == "C":
                c += 1
        return a, b, c

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
        rows = self._store.query(
            "dob_permits",
            where_clause="borough=? AND block=? AND lot=?",
            params=(boro_name, block, lot),
            select="COUNT(*) as cnt",
        )
        return int(rows[0]["cnt"]) if rows else 0


