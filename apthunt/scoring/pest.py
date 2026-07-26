"""
PestScorer — scores listings by pest / vermin exposure.

Combines two independent data sources:

1. **Building-level** — HPD complaints categorised as
   ``major_category='UNSANITARY CONDITION'`` and
   ``minor_category='PESTS'``, looked up by the building's BBL
   (via PLUTO, same approach as ManagementScorer).

2. **Area-level** — 311 Service Requests with ``complaint_type='Rodent'``
   within 100 m (tight radius — pests are hyper-local).

Scoring:
    Both sides are normalised into density-corrected per-household rates
    (raw counts-in-radius are population-density confounded — dense areas
    generate more incident *volume* at identical per-household risk):

    * **Building rate** — empirical-Bayes shrunk per-unit rate:
      ``eb_rate(hpd_count, units, prior, k=10)`` where *prior* is the
      citywide mean of the baseline metric (:func:`baseline_median`).
      This replaces the old ``count / (units + 5)`` + zero-is-perfect
      pinning: only 31% of 1-4-unit buildings ever file HPD pest reports
      (vs 93% of 5+), so *absence of records is non-reporting, not
      cleanliness*.  Under EB, a 3-unit building with 0 records shrinks
      toward the prior (mid-high score, honest thin evidence) while a
      300-unit building with 0 records earns a near-zero rate and a top
      score (strong evidence).  The prior is ``None`` until the first
      ``build_baseline.py`` run — ``eb_rate`` then falls back to the raw
      rate, so EB engages on the second scoring pass and the baseline
      distribution itself converges after one rebuild cycle.
    * **Area rate** — the recency-decayed, distance-kernelled 311 rodent
      sum divided by :func:`kernel_weighted_units` over the *same* 100 m
      radius (same Gaussian kernel on both sides), scaled ×1000 to a
      per-1000-household rate for human-readable numbers.

    Combined baseline metric (documented design):

        pest_rate = pest_per_unit + 0.5 * (pest_rodent_rate / 1000)

    The /1000 converts the area rate back to per-household so both terms
    share units; the 0.5 half-weights block-level 311 reports against
    building-verified HPD complaints.  ``pest_rate`` is scored against
    the frozen citywide baseline distribution (batch percentile fallback
    until the first baseline build), while ``pest_per_unit`` ("this
    building") and ``pest_rodent_rate`` ("this block") stay separate
    components for the UI.

    **Per-building attribution (v6)** — building-level stats are
    resolved per unique listing coordinate — listings in the same
    building share identical source coords; different buildings never
    do — and cached by building identity: HPD stats under
    ``bbl:{normalized_bbl}``, the lot resolution itself under
    ``lot:{lat5},{lon5}``.  Previously the whole blob (building + area)
    was computed once per geohash-7 cell (~150 m) and cached under the
    cell key, so whichever listing resolved first *owned* the cell and
    every other building in it inherited a stranger's building record.
    Area-level 311 rodent stats are a genuine area statistic and keep
    cell-level caching.

    **Building attribution guard** — the nearest-PLUTO-lot match is
    rejected beyond ``BUILDING_MATCH_MAX_M`` (40 m; measured p95 match
    distance is 25 m) so a mis-geocoded listing can't inherit a
    *neighbor's* HPD record.  On rejection the building side is unknown:
    building components go NULL and ``pest_match_uncertain = 1``.

    **New-building rule** — if the matched lot was built within ~3 years
    (:func:`is_new_building`) *and* has zero HPD pest records, the
    building side is "no track record yet", not perfect:
    ``pest_per_unit`` goes NULL and ``pest_new_building = 1``.

    **Combining when the building part is unknown** (either rule above):
    the score is still computed, from the area part plus the prior.  The
    EB estimate with *zero* building evidence collapses to the prior, so
    the building term degrades to ``prior`` — keeping ``pest_rate`` on
    the same scale as the frozen baseline distribution:

        pest_rate = prior + 0.5 * (pest_rodent_rate / 1000)

    (area-only before the first baseline build, when no prior exists).

Output columns:
    pest_hpd_count       INTEGER — HPD pest complaints for the building
                                   (NULL when match rejected)
    pest_rodent_count    INTEGER — 311 rodent complaints within 100 m
    pest_total           INTEGER — combined raw total (NULL when match rejected)
    pest_units           INTEGER — residential units from PLUTO (≥1;
                                   NULL when match rejected)
    pest_weighted        REAL    — hpd_count + decayed/kernelled rodent sum
                                   (NULL when match rejected)
    pest_per_unit        REAL    — building rate: eb_rate(hpd_count, units,
                                   prior, k=10) (NULL when building side unknown)
    pest_rodent_rate     REAL    — area rate: 1000 × weighted rodent sum
                                   / kernel-weighted units (100 m)
    pest_rate            REAL    — combined per-household rate (baseline metric)
    pest_match_uncertain INTEGER — 1 when nearest lot > 40 m (building
                                   attribution rejected), else 0
    pest_new_building    INTEGER — 1 when lot built within ~3 yr with zero
                                   HPD pest records ("no track record"), else 0
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import (
    baseline_median,
    baseline_scores,
    decay_weight,
    eb_rate,
)
from apthunt.scoring.utils import (
    BUILDING_MATCH_MAX_M,
    dedupe_by_geohash,
    find_nearest_row,
    is_new_building,
    kernel_weighted_units,
    normalize_bbl,
    percentile_scores,
    pluto_units,
)


RADIUS_M = 100

# Cache version — bumped to v6 when building-level stats moved from
# geohash-cell keys to per-building keys (``bbl:{bbl}`` / ``lot:{lat5},
# {lon5}``); cell-keyed building stats let one building's HPD record
# bleed onto every neighbor in the same ~150 m cell.  The area (311
# rodent) blob stays under the plain geohash key with this source.
CACHE_SOURCE = "pest_v6"


def _coord_key(lat: float, lon: float) -> str:
    """Canonical per-building coordinate key, ``"{lat5},{lon5}"``.

    Listings in the same building share identical source coordinates
    (5-decimal rounding ≈ 1 m just canonicalises float noise), while
    different buildings never share them — unlike a geohash-7 cell
    (~150 m) which routinely spans many buildings.
    """
    return f"{round(lat, 5)},{round(lon, 5)}"


class PestScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "pest"

    # Citywide baseline metric (sampled by scripts/build_baseline.py).
    # zero_perfect is False: absence of HPD records is mostly non-reporting
    # (31% filing rate in 1-4-unit buildings) — the EB-shrunk rate, not a
    # pinned 100, decides how much credit a zero-count building earns.
    baseline_component = "pest_rate"
    baseline_reverse = True
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "pest_hpd_count": "INTEGER",
            "pest_rodent_count": "INTEGER",
            "pest_total": "INTEGER",
            "pest_units": "INTEGER",
            "pest_weighted": "REAL",
            "pest_per_unit": "REAL",
            "pest_rodent_rate": "REAL",
            "pest_rate": "REAL",
            "pest_match_uncertain": "INTEGER",
            "pest_new_building": "INTEGER",
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
        self._store.ensure_downloaded("hpd_complaints", quiet=True)
        self._store.ensure_downloaded("noise", quiet=True)

        today_ord = datetime.now().toordinal()

        # Citywide prior for the EB-shrunk building rate.  None until the
        # first build_baseline.py run — eb_rate then falls back to the raw
        # per-unit rate, so EB engages on the next pass and the pipeline
        # converges after one baseline rebuild cycle.
        prior = baseline_median(conn, self.name)

        # --- Area-level: 311 rodent within 100 m, per geohash cell -----
        # A genuine area statistic — cell-level dedupe + caching is
        # correct here and stays (one lookup per block).
        gh_map = dedupe_by_geohash(listings)
        area_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, CACHE_SOURCE)
            if cached is not None:
                area_stats[gh] = cached
                continue

            rodent_count, rodent_weighted = self._rodent_counts(
                lat, lon, today_ord,
            )

            # Area rate — density-corrected per-1000-households: same
            # Gaussian kernel + radius on numerator (rodent sum) and
            # denominator (kernel-weighted PLUTO units).
            kw_units = kernel_weighted_units(self._store, lat, lon, RADIUS_M)

            stats = {
                "pest_rodent_count": rodent_count,
                "pest_rodent_weighted": round(rodent_weighted, 4),
                "pest_rodent_rate": round(
                    1000.0 * rodent_weighted / kw_units, 4,
                ),
            }
            area_stats[gh] = stats
            self._cache.put(gh, CACHE_SOURCE, stats)

        # --- Building-level: resolve the PLUTO lot PER LISTING COORD ---
        # Never per geohash cell: a cell spans many buildings, and the
        # first listing to compute a cell would otherwise "own" it,
        # handing its building record to every neighbor in the cell.
        lot_by_coord: dict[str, dict] = {}
        for lst in listings:
            ck = _coord_key(lst["lat"], lst["lon"])
            if ck not in lot_by_coord:
                lot_by_coord[ck] = self._resolve_lot(
                    ck, lst["lat"], lst["lon"],
                )

        # --- Per-listing assembly: building part (by BBL) + area part
        # (by cell) may come from different cache keys.
        building_memo: dict[str, dict] = {}
        per_listing: list[dict] = []
        for lst in listings:
            area = area_stats[lst["geohash"]]
            lot = lot_by_coord[_coord_key(lst["lat"], lst["lon"])]
            building = self._building_stats(lot, prior, building_memo)

            hpd_count = building["pest_hpd_count"]
            total = (
                hpd_count + area["pest_rodent_count"]
                if hpd_count is not None
                else None
            )
            weighted = (
                round(hpd_count + area["pest_rodent_weighted"], 4)
                if hpd_count is not None
                else None
            )

            # Combined baseline metric: building rate + half-weighted
            # area rate (converted back to per-household via /1000).
            # When the building part is unknown (guard reject or new
            # building), the EB estimate with zero building evidence
            # collapses to the prior — so the building term degrades to
            # `prior`, keeping pest_rate on the baseline's scale
            # (area-only before the first baseline build).
            per_unit = building["pest_per_unit"]
            if per_unit is not None:
                building_term = per_unit
            elif prior is not None:
                building_term = prior
            else:
                building_term = 0.0
            pest_rate = round(
                building_term + 0.5 * (area["pest_rodent_rate"] / 1000.0), 4,
            )

            per_listing.append({
                "pest_hpd_count": hpd_count,
                "pest_rodent_count": area["pest_rodent_count"],
                "pest_total": total,
                "pest_units": building["pest_units"],
                "pest_weighted": weighted,
                "pest_per_unit": per_unit,
                "pest_rodent_rate": area["pest_rodent_rate"],
                "pest_rate": pest_rate,
                "pest_match_uncertain": building["pest_match_uncertain"],
                "pest_new_building": building["pest_new_building"],
            })

        # Score pest_rate against the frozen citywide baseline (lower =
        # better).  No zero_is_perfect pinning: a zero-count building's
        # credit is decided by its EB-shrunk rate (exposure-aware), since
        # absence of records is mostly non-reporting in small buildings.
        raw = [stats["pest_rate"] for stats in per_listing]
        pct_scores = baseline_scores(conn, self.name, raw, reverse=True)
        if pct_scores is None:
            # Fallback until the first baseline build
            pct_scores = percentile_scores(raw, reverse=True)

        results: list[ScorerResult] = []
        for lst, stats, pct in zip(listings, per_listing, pct_scores):
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=pct,
                    components=stats,
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_lot(self, coord_key: str, lat: float, lon: float) -> dict:
        """Resolve the nearest PLUTO lot for one listing coordinate.

        Attribution-guarded: beyond 40 m the nearest lot is likely the
        *wrong* building (measured p95 match distance: 25 m) — reject
        (``matched=0``) rather than inherit a neighbor's HPD record.

        Cached under ``lot:{lat5},{lon5}`` (per-building key) so
        repeated bbox queries for the same coordinates are avoided.
        """
        key = f"lot:{coord_key}"
        cached = self._cache.get(key, CACHE_SOURCE)
        if cached is not None:
            return cached

        pluto_rows = self._store.query_bbox(
            "pluto", lat, lon, delta=0.0015,
        )
        nearest = find_nearest_row(
            pluto_rows, lat, lon, max_dist_m=BUILDING_MATCH_MAX_M,
        )

        if nearest is None:
            lot = {
                "matched": 0,
                "bbl": None,
                "units": None,
                "new_building": 0,
            }
        else:
            raw_bbl = nearest.get("bbl")
            lot = {
                "matched": 1,
                "bbl": normalize_bbl(raw_bbl) if raw_bbl else None,
                "units": pluto_units(nearest),
                "new_building": 1 if is_new_building(nearest) else 0,
            }

        self._cache.put(key, CACHE_SOURCE, lot)
        return lot

    def _building_stats(
        self, lot: dict, prior, memo: dict,
    ) -> dict:
        """Building-level HPD pest stats for one resolved lot.

        Cached in BlockCache under ``bbl:{normalized_bbl}`` — a building
        identity, never a geohash cell — plus an in-run memo.  A lot
        that matched but carries no BBL is computed inline (hpd_count is
        0 without a BBL to query, so this is cheap).
        """
        if not lot["matched"]:
            # Attribution guard rejected the match: the building side
            # is unknown, not zero.  Building components go NULL.
            return {
                "pest_hpd_count": None,
                "pest_units": None,
                "pest_per_unit": None,
                "pest_match_uncertain": 1,
                "pest_new_building": 0,
            }

        bbl = lot["bbl"]
        if bbl:
            if bbl in memo:
                return memo[bbl]
            cached = self._cache.get(f"bbl:{bbl}", CACHE_SOURCE)
            if cached is not None:
                memo[bbl] = cached
                return cached

        hpd_count = self._hpd_pest_count(bbl)
        units = lot["units"]
        new_building = 0
        if lot["new_building"] and hpd_count == 0:
            # Built within ~3 yr with zero HPD pest records: "no track
            # record yet", not evidence of cleanliness.
            new_building = 1
            per_unit = None
        else:
            # Building rate — EB-shrunk per-unit rate (replaces the
            # old +5 damping): thin evidence shrinks toward the
            # citywide prior; a large zero-count building keeps a
            # near-zero rate on the strength of its exposure.
            per_unit = round(eb_rate(hpd_count, units, prior, k=10.0), 4)

        stats = {
            "pest_hpd_count": hpd_count,
            "pest_units": units,
            "pest_per_unit": per_unit,
            "pest_match_uncertain": 0,
            "pest_new_building": new_building,
        }
        if bbl:
            memo[bbl] = stats
            self._cache.put(f"bbl:{bbl}", CACHE_SOURCE, stats)
        return stats

    def _hpd_pest_count(self, bbl) -> int:
        """Count HPD pest complaints for a normalized BBL."""
        if not bbl:
            return 0

        rows = self._store.query(
            "hpd_complaints",
            where_clause=(
                "bbl = ? "
                "AND major_category = 'UNSANITARY CONDITION' "
                "AND minor_category = 'PESTS'"
            ),
            params=(bbl,),
            select="COUNT(*) as cnt",
        )
        return int(rows[0]["cnt"]) if rows else 0

    def _rodent_counts(
        self, lat: float, lon: float, today_ord: int
    ) -> tuple:
        """311 rodent complaints within RADIUS_M metres.

        Returns ``(plain_count, weighted_sum)`` where each complaint is
        weighted by recency decay (half-life 180 d) times a Gaussian
        distance kernel exp(-(d / (RADIUS_M/2))**2).
        """
        rows = self._store.query_circle(
            "noise",
            lat=lat,
            lon=lon,
            radius_m=RADIUS_M,
            select="complaint_type,created_date",
        )
        count = 0
        weighted = 0.0
        sigma = RADIUS_M / 2.0
        for r in rows:
            if r.get("complaint_type") != "Rodent":
                continue
            count += 1
            dist = float(r.get("_dist_m") or 0.0)
            kernel = math.exp(-((dist / sigma) ** 2))
            weighted += kernel * decay_weight(
                (r.get("created_date") or "")[:10], today_ord,
            )
        return count, weighted
