"""
BedbugScorer — scores listings by the building's bedbug filing history.

Data source: ``ds_bedbug_reporting`` — HPD annual owner bedbug filings
(one row per building per filing year).

Reporting reality (measured): only ~31% of 1-4-unit buildings ever file,
vs ~93% of 5+-unit buildings.  Absence of filings is therefore mostly
*non-reporting*, not cleanliness — so zero filings must NOT be scored as
"pristine".  Instead the per-unit rate is empirical-Bayes shrunk toward
the citywide prior (``eb_rate``): a 3-unit building with no records
lands mid-high (honest thin evidence), while a 300-unit building with no
records earns a top score (300 units of real exposure with nothing
reported is strong evidence).  For 5+-unit buildings a missing filing is
itself mild negative evidence (93% file): the EB prior dominates
naturally, and ``bedbug_never_filed=1`` is emitted so the UI can say
"owner has never filed the required annual bedbug report".

Method:
    Each listing's own coordinates (rounded to 5 decimals, ~1m —
    listings in the same building share coordinates; different
    buildings never do) resolve to the nearest PLUTO lot / BBL —
    guarded by ``BUILDING_MATCH_MAX_M`` (40m): when the nearest lot is
    farther than that, the attribution is likely the wrong building, so
    the score is None and ``bedbug_match_uncertain=1`` is emitted (this
    dimension is entirely building-specific — there is no area
    component to salvage).

    Building stats are cached per BBL (``bbl:<bbl>``), never per
    geohash cell: a ~150m cell mixes neighboring buildings, and caching
    under the cell key let whichever listing resolved the cell first
    "own" it — every other building in the cell then inherited a
    stranger's building record.  The lot resolution itself is cached
    per coordinate (``lot:<lat5>,<lon5>``) to avoid repeated bbox
    queries.

    Buildings built within ~3 years with no filing history score None
    with ``bedbug_new_building=1`` — a brand-new tower has "no track
    record yet", not a perfect record.

    Otherwise the building's multi-year filing history is aggregated:

        weighted = Σ over filings of (infested + 2 * re_infested)
                   × recency decay (local half-life 2 years — bedbug
                     history stays relevant for years)
        rate     = eb_rate(weighted, units, prior, k=10.0)
                 = (weighted + k * prior) / (units + k)

    where ``prior = baseline_median(conn, "bedbug")`` — the citywide mean
    rate from the frozen baseline.  On the very first pass (before
    ``build_baseline.py`` has ever run) the prior is None and
    ``eb_rate`` falls back to the raw rate; the first baseline build
    then freezes a distribution of raw rates, and every subsequent
    scoring pass shrinks against it — the EB behaviour converges after
    one baseline cycle.

    Re-infestations are double-weighted: a building that keeps getting
    re-infested has a treatment/eradication problem, not bad luck.

    ``bedbug_rate`` is scored against the frozen citywide baseline
    (reverse: lower is better; zero is NOT pinned to perfect — see
    reporting reality above).  Before the first baseline build, an
    absolute exponential fallback is used:
    score = 100 · 0.5^(rate / 0.05).

Output columns:
    bedbug_filings          INTEGER — filing rows found for the building
    bedbug_infested_total   REAL    — Σ infested_dwelling_unit_count
    bedbug_reinfested_total REAL    — Σ re_infested_dwelling_unit
    bedbug_rate             REAL    — EB-shrunk recency-weighted per-unit rate
    bedbug_match_uncertain  INTEGER — 1 when no PLUTO lot within 40m
    bedbug_new_building     INTEGER — 1 when built <~3yr ago with no records
    bedbug_never_filed      INTEGER — 1 when units >= 5 and zero filings
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_median, baseline_scores, eb_rate
from apthunt.scoring.utils import (
    BUILDING_MATCH_MAX_M,
    find_nearest_row,
    is_new_building,
    normalize_bbl,
    pluto_units,
)

# Bedbug history matters for years — decay far slower than the shared
# 6-month helper (half-life = 2 years).
HALF_LIFE_DAYS = 365.0 * 2

# Empirical-Bayes shrinkage strength: pseudo-units of prior evidence.
# A building needs ~10 real units before its own record outweighs the
# citywide prior.
EB_K = 10.0

# Buildings at/above this unit count are legally expected to file
# annually (~93% do) — zero filings there is itself a signal.
FILING_EXPECTED_UNITS = 5

# Absolute fallback: rate at which the score halves (one recent
# infested unit in a ~15-unit building ≈ 0.05).
FALLBACK_HALF_SCORE_RATE = 0.05

# v3: building attribution fixed — lots are resolved per listing
# coordinate (not per ~150m geohash cell) and building stats are cached
# per BBL ("bbl:<bbl>"), with the lot resolution cached per coordinate
# ("lot:<lat5>,<lon5>").  v2 cached stats under the cell key, so every
# building in a cell inherited whichever lot was resolved first.
CACHE_KEY = "bedbug_v3"


def _bedbug_decay(date_str, today_ord: int) -> float:
    """Recency weight with a 2-year half-life (local — bedbug-specific)."""
    try:
        d = datetime.fromisoformat(str(date_str)[:10]).toordinal()
    except (ValueError, TypeError):
        return 0.5
    age = max(0, today_ord - d)
    return 0.5 ** (age / HALF_LIFE_DAYS)


def _num(val) -> float:
    """Coerce SODA count fields (strings / REALs / None) to float."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _coord_key(lst: dict) -> tuple[float, float]:
    """5-decimal (~1m) coordinate key for a listing.

    Listings in the same building share exact coordinates; different
    buildings never do — so this is the correct dedupe granularity for
    building-level attribution (a geohash-7 cell, ~150m, is not).
    """
    return (round(lst["lat"], 5), round(lst["lon"], 5))


class BedbugScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "bedbug"

    # Citywide baseline metric (sampled by scripts/build_baseline.py)
    baseline_component = "bedbug_rate"
    baseline_reverse = True
    # Zero filings is mostly non-reporting (31% of small buildings file),
    # not cleanliness — never pin it to a perfect score.
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "bedbug_filings": "INTEGER",
            "bedbug_infested_total": "REAL",
            "bedbug_reinfested_total": "REAL",
            "bedbug_rate": "REAL",
            "bedbug_match_uncertain": "INTEGER",
            "bedbug_new_building": "INTEGER",
            "bedbug_never_filed": "INTEGER",
            "bedbug_not_required": "INTEGER",
        }

    # ------------------------------------------------------------------
    # Main scoring entry point
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("pluto", quiet=True)
        self._store.ensure_downloaded("bedbug_reporting", quiet=True)

        today_ord = datetime.now().toordinal()

        # Per-listing coordinate dedupe (NOT geohash cells): listings in
        # the same building share exact coordinates; different buildings
        # never do.  Deduping to ~150m cells let the first listing's lot
        # "own" the cell and gave neighboring buildings its record.
        coord_map: dict[tuple[float, float], tuple[float, float]] = {}
        for lst in listings:
            coord_map.setdefault(_coord_key(lst), (lst["lat"], lst["lon"]))

        coord_stats: dict[tuple[float, float], dict] = {}
        bbl_stats: dict[str, dict] = {}  # per-run memo: coords sharing a BBL

        for ckey, (lat, lon) in coord_map.items():
            # --- PLUTO lot resolution (BBL), 40m attribution guard,
            #     cached per coordinate ------------------------------------
            lot_key = f"lot:{ckey[0]},{ckey[1]}"
            lot = self._cache.get(lot_key, CACHE_KEY)
            if lot is None:
                try:
                    lot = self._resolve_lot(lat, lon)
                except sqlite3.OperationalError:
                    # Dataset still downloading — bail without crashing.
                    return [
                        ScorerResult(listing_id=lst["id"], score=None, components={})
                        for lst in listings
                    ]
                self._cache.put(lot_key, CACHE_KEY, lot)

            if lot.get("bedbug_match_uncertain"):
                coord_stats[ckey] = {"bedbug_match_uncertain": 1}
                continue

            # --- Building filings via BBL, cached per BBL ---------------
            bbl = lot["bbl"]
            stats = bbl_stats.get(bbl)
            if stats is None:
                stats = self._cache.get(f"bbl:{bbl}", CACHE_KEY)
            if stats is None:
                try:
                    stats = self._building_stats(bbl, lot, today_ord)
                except sqlite3.OperationalError:
                    return [
                        ScorerResult(listing_id=lst["id"], score=None, components={})
                        for lst in listings
                    ]
                self._cache.put(f"bbl:{bbl}", CACHE_KEY, stats)

            bbl_stats[bbl] = stats
            coord_stats[ckey] = stats

        # EB-shrink the raw evidence at score time (not in the cache) so
        # a baseline rebuild updates every rate without cache invalidation.
        # prior is None until the first baseline build — eb_rate then
        # falls back to the raw rate (one-cycle convergence, see module
        # docstring).
        prior = baseline_median(conn, self.name)

        raw: list = []
        for lst in listings:
            stats = coord_stats[_coord_key(lst)]
            if stats.get("bedbug_match_uncertain") or stats.get("bedbug_new_building"):
                raw.append(None)
            elif stats["bedbug_units"] < 3 and stats["bedbug_filings"] == 0:
                # Buildings under 3 units aren't required to file bedbug
                # reports at all — zero filings there is pure non-information,
                # not evidence in either direction. Scoring it (even EB-
                # anchored) just proxies building size. Honest answer: None.
                stats["bedbug_not_required"] = 1
                raw.append(None)
            else:
                raw.append(
                    round(
                        eb_rate(
                            stats["bedbug_weighted"],
                            stats["bedbug_units"],
                            prior,
                            k=EB_K,
                        ),
                        4,
                    )
                )

        # Score bedbug_rate against the frozen citywide baseline
        # (lower = better; zero is NOT perfect — EB shrinkage already
        # encodes how much a zero count is actually worth).
        pct_scores = baseline_scores(conn, self.name, raw, reverse=True)
        if pct_scores is None:
            # Absolute fallback until the first baseline build:
            # exponential in the per-unit rate, halving every 0.05.
            pct_scores = [
                round(100.0 * (0.5 ** (r / FALLBACK_HALF_SCORE_RATE)), 1)
                if r is not None else None
                for r in raw
            ]

        results: list[ScorerResult] = []
        for lst, pct, rate in zip(listings, pct_scores, raw):
            stats = coord_stats[_coord_key(lst)]

            if stats.get("bedbug_match_uncertain"):
                # No lot within 40m: building attribution is unreliable
                # and this dimension is entirely building-specific.
                results.append(
                    ScorerResult(
                        listing_id=lst["id"],
                        score=None,
                        components={"bedbug_match_uncertain": 1},
                    )
                )
                continue

            if stats.get("bedbug_new_building"):
                # Built within ~3 years, zero records: no track record
                # yet — unknown, not perfect.
                results.append(
                    ScorerResult(
                        listing_id=lst["id"],
                        score=None,
                        components={
                            "bedbug_new_building": 1,
                            "bedbug_filings": 0,
                            "bedbug_infested_total": 0.0,
                            "bedbug_reinfested_total": 0.0,
                        },
                    )
                )
                continue

            if stats.get("bedbug_not_required"):
                # Under 3 units: no legal filing requirement — zero filings
                # is non-information, not evidence. Unknown, not scored.
                results.append(
                    ScorerResult(
                        listing_id=lst["id"],
                        score=None,
                        components={
                            "bedbug_not_required": 1,
                            "bedbug_filings": 0,
                        },
                    )
                )
                continue

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=pct,
                    components={
                        "bedbug_filings": stats["bedbug_filings"],
                        "bedbug_infested_total": stats["bedbug_infested_total"],
                        "bedbug_reinfested_total": stats["bedbug_reinfested_total"],
                        "bedbug_rate": rate,
                        "bedbug_never_filed": stats.get("bedbug_never_filed", 0),
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_lot(self, lat: float, lon: float) -> dict:
        """Resolve one listing coordinate to its nearest PLUTO lot.

        Returns a minimal cacheable lot record::

            {"bbl": <normalized>, "unitsres": ..., "yearbuilt": ...}

        or ``{"bedbug_match_uncertain": 1}`` when the 40m guard rejects
        the nearest lot (or none found, or the lot has no BBL — a lot
        without a BBL can't be joined to filings, same "attribution
        unknown" outcome).  ``unitsres``/``yearbuilt`` are carried so a
        BBL-stats cache miss can recompute without re-querying PLUTO.
        """
        pluto_rows = self._store.query_bbox(
            "pluto", lat, lon, delta=0.0015,
        )
        nearest = find_nearest_row(
            pluto_rows, lat, lon, max_dist_m=BUILDING_MATCH_MAX_M,
        )
        if nearest is None:
            # 40m guard rejected the nearest lot (or none found): the
            # building-specific evidence is unknown, not zero.
            return {"bedbug_match_uncertain": 1}
        raw_bbl = nearest.get("bbl")
        if not raw_bbl:
            return {"bedbug_match_uncertain": 1}
        return {
            "bbl": normalize_bbl(raw_bbl),
            "unitsres": nearest.get("unitsres"),
            "yearbuilt": nearest.get("yearbuilt"),
        }

    def _building_stats(self, bbl: str, lot: dict, today_ord: int) -> dict:
        """Aggregate the building's raw bedbug filing evidence.

        ``lot`` is the minimal lot record from :meth:`_resolve_lot`
        (needs ``unitsres``/``yearbuilt`` for the no-filings paths).

        Returns cacheable *raw* evidence (weighted count + units + flags);
        the EB rate itself is computed at score time against the current
        baseline prior.
        """
        rows = self._store.query(
            "bedbug_reporting",
            where_clause="bbl = ?",
            params=(bbl,),
            select=(
                "of_dwelling_units,infested_dwelling_unit_count,"
                "re_infested_dwelling_unit,filing_date"
            ),
        )

        if not rows:
            units = float(pluto_units(lot))
            if is_new_building(lot):
                # Too new to have a record — "no track record yet".
                return {"bedbug_new_building": 1}
            stats = {
                "bedbug_filings": 0,
                "bedbug_infested_total": 0.0,
                "bedbug_reinfested_total": 0.0,
                "bedbug_weighted": 0.0,
                "bedbug_units": units,
            }
            if units >= FILING_EXPECTED_UNITS:
                # 93% of 5+-unit buildings file: a missing filing is mild
                # negative evidence, not pristine.  eb_rate already keeps
                # the prior dominant for the score; this flag lets the UI
                # say "owner has never filed the required annual report".
                stats["bedbug_never_filed"] = 1
            return stats

        infested_total = 0.0
        reinfested_total = 0.0
        weighted = 0.0
        units = 0.0

        for r in rows:
            infested = _num(r.get("infested_dwelling_unit_count"))
            reinfested = _num(r.get("re_infested_dwelling_unit"))
            infested_total += infested
            reinfested_total += reinfested
            weighted += (infested + 2.0 * reinfested) * _bedbug_decay(
                r.get("filing_date"), today_ord,
            )
            units = max(units, _num(r.get("of_dwelling_units")))

        if units <= 0:
            units = float(pluto_units(lot))

        return {
            "bedbug_filings": len(rows),
            "bedbug_infested_total": round(infested_total, 1),
            "bedbug_reinfested_total": round(reinfested_total, 1),
            "bedbug_weighted": round(weighted, 4),
            "bedbug_units": units,
        }
