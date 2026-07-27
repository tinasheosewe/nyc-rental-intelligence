"""
CrimeScorer — scores listings by crime density in their surrounding area.

Uses pre-downloaded NYPD Complaints (``ds_crime``) with Haversine
circle queries cached in BlockCache by geohash, plus an NYPD
shootings overlay (``ds_shootings``).

Scoring:
    Weight by severity: FELONY ×3, MISDEMEANOR ×1.5, VIOLATION ×1.
    Each incident additionally weighted by recency decay (half-life
    180 days) and a Gaussian distance kernel so a felony last week
    next door outweighs one 18 months ago at the radius edge.

    Context multipliers (when the re-downloaded ds_crime carries
    ofns_desc / prem_typ_desc / cmplnt_fr_tm — degrades to ×1.0 when
    the columns are absent): violent/street offenses ×1.5, petit
    larceny ×0.4 (retail-theft inflation); chain/department/grocery
    store premises ×0.4, STREET ×1.2, RESIDENCE ×1.1; night
    (20:00–04:59) ×1.5.  Multipliers apply per incident to the
    decayed weighted sum (the rate numerator).

    Shootings overlay: gun violence is the strongest street-safety
    signal and too rare to move the complaint sum, so ds_shootings
    incidents get their own term — Gaussian kernel (σ=200m, radius
    400m) × recency decay over the 2-year window, each shooting
    weighted ×8 relative to a felony complaint.  Folded into the
    rate numerator.  ds_shootings coordinates are being repaired by
    a swap-fix post-process; if MIN(latitude) < 0 the table is still
    unrepaired and the term is skipped gracefully.

    The numerator (complaints + shootings) is divided by the current
    month's seasonal factor for ds_crime (complaint volume is strongly
    seasonal, so July scores would otherwise read systematically
    different from January scores), then by kernel-weighted PLUTO
    residential units (same kernel + radius) and ×1000 → incidents
    per 1000 households, correcting the population-density confound
    (crowded ≠ dangerous).  Score the rate against the frozen citywide
    baseline distribution (lower crime = higher score); falls back
    to the cached-block median until the first baseline build.

    Trend: compares recent-half (last 6 months) vs older-half
    to produce a trend ratio.  < 1.0 = improving, > 1.0 = worsening.
    Suppressed (1.0/"stable") when the feed's publication lag leaves
    the recent window < 70% covered.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores, decay_weight, seasonal_factor
from apthunt.scoring.utils import (
    TREND_MIDPOINT,
    compute_trend,
    dedupe_by_geohash,
    kernel_weighted_units,
    median_inverse_scores,
)

# ── Context multipliers (offense × premise × night) ──────────────────

# Violent / street offenses that dominate the lived experience of an
# unsafe block (substring match on upper-cased ofns_desc; "MURDER"
# catches "MURDER & NON-NEGL. MANSLAUGHTER", "WEAPONS" catches
# "DANGEROUS WEAPONS").
_VIOLENT_OFFENSE_KEYS = (
    "ROBBERY",
    "FELONY ASSAULT",
    "BURGLARY",
    "GRAND LARCENY OF MOTOR VEHICLE",
    "RAPE",
    "MURDER",
    "WEAPONS",
)

# Retail premises where complaint volume is shoplifting-inflated and
# says little about residential street safety.
_STORE_PREMISE_KEYS = ("CHAIN STORE", "DEPARTMENT STORE", "GROCERY", "SUPERMARKET")


def _context_multiplier(ofns, prem, tm) -> float:
    """Per-incident relevance multiplier = offense × premise × night.

    Any missing/blank field contributes ×1.0, so rows from the
    pre-re-download schema (fields absent) reproduce current weights.
    """
    mult = 1.0
    o = (ofns or "").upper()
    if o:
        if "PETIT LARCENY" in o:
            mult *= 0.4
        elif any(k in o for k in _VIOLENT_OFFENSE_KEYS):
            mult *= 1.5
    p = (prem or "").upper()
    if p:
        if any(k in p for k in _STORE_PREMISE_KEYS):
            mult *= 0.4
        elif "STREET" in p:
            mult *= 1.2
        elif "RESIDENCE" in p:
            mult *= 1.1
    t = str(tm or "")
    try:
        hour = int(t.split(":")[0])
    except (ValueError, IndexError):
        hour = None
    if hour is not None and (hour >= 20 or hour <= 4):
        mult *= 1.5
    return mult


class CrimeScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "crime"

    # Baseline metric: decayed, context- and distance-kernel-weighted
    # severity sum (complaints + shootings overlay), seasonally normalized,
    # per 1000 kernel-weighted households.  Raw counts-in-radius are
    # population-density confounded (crowded ≠ dangerous); the per-household
    # rate is not.  Lower is better; zero crime is perfect.
    baseline_component = "crime_rate_per_khh"
    baseline_reverse = True
    baseline_zero_perfect = True

    def columns(self) -> dict[str, str]:
        return {
            "crime_felony_count": "INTEGER",
            "crime_misdemeanor_count": "INTEGER",
            "crime_violation_count": "INTEGER",
            "crime_weighted_total": "REAL",
            "crime_weighted_decayed": "REAL",
            "crime_shootings_count": "INTEGER",
            "crime_shootings_weighted": "REAL",
            "crime_units_weighted": "REAL",
            "crime_rate_per_khh": "REAL",
            "crime_trend_ratio": "REAL",
            "crime_trend_direction": "TEXT",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        # "crime" is a derived dataset: the data layer merges the historic
        # and YTD upstream feeds into one ds_crime table (deduped, one date
        # column). Scorers never see the seam.
        self._store.ensure_downloaded("crime", quiet=True)

        # Parameters
        RADIUS_M = 400
        # Gaussian kernel sigma — half the query radius
        SIGMA_M = RADIUS_M / 2.0
        # Severity weights
        WEIGHTS = {"FELONY": 3.0, "MISDEMEANOR": 1.5, "VIOLATION": 1.0}
        # Shootings overlay: kernel σ=200m over a 400m radius, 2-year window,
        # each shooting ×8 relative to a felony complaint.
        SHOOT_RADIUS_M = 400
        SHOOT_SIGMA_M = 200.0
        SHOOT_WEIGHT = 8.0 * WEIGHTS["FELONY"]
        SHOOT_MAX_AGE_DAYS = 730
        # Recency decay reference — computed once per batch
        today_ord = datetime.now().toordinal()

        # TREND LAG GUARD: the NYPD feed lags roughly a quarter behind, so
        # the recent-6-months trend window is systematically under-filled —
        # every block then reads as a huge "crime drop" (a data artifact,
        # not a trend).  Check the dataset's global freshness once per call:
        # if the data covers < 70% of the recent window, suppress the trend
        # (ratio 1.0 / "stable") for ALL blocks rather than let a half-empty
        # window masquerade as improvement.
        suppress_trend = True
        try:
            fresh = self._store.query("crime", select="MAX(cmplnt_fr_dt) as mx")
            mx = (fresh[0].get("mx") or "")[:10] if fresh else ""
            max_ord = datetime.fromisoformat(mx).toordinal()
            window_start_ord = today_ord - 182
            coverage = max(0.0, min(1.0, (max_ord - window_start_ord) / 182.0))
            suppress_trend = coverage < 0.7
        except (ValueError, TypeError, IndexError):
            pass  # unparseable/missing freshness → keep trend suppressed

        # CONTEXT PROBE (once per call): the offense/premise/time columns
        # arrive with the re-downloaded ds_crime; until then degrade to the
        # current (uncontextualized) weights.
        has_context = True
        try:
            self._store.query(
                "crime", select="ofns_desc,prem_typ_desc,cmplnt_fr_tm", limit=1
            )
        except sqlite3.OperationalError:
            has_context = False

        # SHOOTINGS PROBE (once per call): table must exist AND have been
        # through the swap-fix post-process — an unrepaired table has lat/lon
        # swapped, so MIN(latitude) < 0 (it holds NYC longitudes).  Skip the
        # overlay gracefully in either case.
        shootings_ok = False
        try:
            srow = self._store.query("shootings", select="MIN(latitude) AS mn")
            mn = srow[0].get("mn") if srow else None
            shootings_ok = mn is not None and float(mn) > 0
        except Exception:
            shootings_ok = False

        # Seasonal normalization for the month of scoring — applied at emit
        # time (not baked into the cache) so cached blocks stay valid across
        # month boundaries.
        seasonal = seasonal_factor(conn, "ds_crime", "cmplnt_fr_dt")

        # Cache key: v6 = context multipliers + shootings overlay + seasonal
        # split out of the cached stats.  Data availability is part of the
        # key so blocks cached against today's DB recompute automatically
        # once the re-downloaded columns / repaired shootings land.
        cache_key = "crime_v6:c%ds%d" % (int(has_context), int(shootings_ok))

        crime_select = "law_cat_cd,cmplnt_fr_dt"
        if has_context:
            crime_select += ",ofns_desc,prem_typ_desc,cmplnt_fr_tm"

        # Get all unique geohashes for this batch
        geohash_to_latlon = dedupe_by_geohash(listings)
        # Fetch/calc for each geohash
        block_stats = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            cached = self._cache.get(gh, cache_key)
            if cached is not None:
                block_stats[gh] = cached
                continue
            # Query local DataStore — include date for trend bucketing
            rows = self._store.query_circle(
                "crime",
                lat=lat,
                lon=lon,
                radius_m=RADIUS_M,
                select=crime_select,
            )
            # Count by severity
            fel, mis, vio = 0, 0, 0
            decayed = 0.0
            recent_w, older_w = 0.0, 0.0
            for r in rows:
                cat = (r.get("law_cat_cd") or "").upper()
                w = WEIGHTS.get(cat, 0.0)
                if cat == "FELONY":
                    fel += 1
                elif cat == "MISDEMEANOR":
                    mis += 1
                elif cat == "VIOLATION":
                    vio += 1
                else:
                    continue
                # Bucket by date for trend
                dt = (r.get("cmplnt_fr_dt") or "")[:10]
                if dt >= TREND_MIDPOINT:
                    recent_w += w
                else:
                    older_w += w
                # Decayed + distance-kernel + context weighted contribution
                dist_m = float(r.get("_dist_m") or 0.0)
                kernel = math.exp(-((dist_m / SIGMA_M) ** 2))
                mult = (
                    _context_multiplier(
                        r.get("ofns_desc"),
                        r.get("prem_typ_desc"),
                        r.get("cmplnt_fr_tm"),
                    )
                    if has_context
                    else 1.0
                )
                decayed += w * mult * kernel * decay_weight(dt, today_ord)

            weighted = fel * WEIGHTS["FELONY"] + mis * WEIGHTS["MISDEMEANOR"] + vio * WEIGHTS["VIOLATION"]

            # Shootings overlay: kernel(σ=200m) + decayed count over the
            # 2-year window, ×8 a felony complaint, folded into the numerator.
            shoot_count = 0
            shoot_weighted = 0.0
            if shootings_ok:
                srows = self._store.query_circle(
                    "shootings",
                    lat=lat,
                    lon=lon,
                    radius_m=SHOOT_RADIUS_M,
                    select="occur_date",
                )
                for r in srows:
                    sdt = (r.get("occur_date") or "")[:10]
                    try:
                        age = today_ord - datetime.fromisoformat(sdt).toordinal()
                    except (ValueError, TypeError):
                        age = None  # table is 2yr-windowed at download; keep
                    if age is not None and age > SHOOT_MAX_AGE_DAYS:
                        continue
                    dist_m = float(r.get("_dist_m") or 0.0)
                    kernel = math.exp(-((dist_m / SHOOT_SIGMA_M) ** 2))
                    shoot_count += 1
                    shoot_weighted += SHOOT_WEIGHT * kernel * decay_weight(sdt, today_ord)

            # Per-household rate denominator: same Gaussian kernel + radius
            # (PLUTO residential units) so density cancels.
            units = kernel_weighted_units(self._store, lat, lon, RADIUS_M)

            # Trend ratio: recent / older.  < 1.0 = improving
            trend_ratio, direction = compute_trend(recent_w, older_w)

            block_stats[gh] = {
                "crime_felony_count": fel,
                "crime_misdemeanor_count": mis,
                "crime_violation_count": vio,
                "crime_weighted_total": weighted,
                "crime_weighted_decayed": round(decayed, 3),
                "crime_shootings_count": shoot_count,
                "crime_shootings_weighted": round(shoot_weighted, 3),
                "crime_units_weighted": round(units, 1),
                "crime_trend_ratio": trend_ratio,
                "crime_trend_direction": direction,
            }
            self._cache.put(gh, cache_key, block_stats[gh])

        # Per-1000-household rate, computed at emit time: numerator =
        # (context-weighted complaints + shootings overlay) normalized by
        # this month's seasonal factor; ×1000 / kernel-weighted units.
        rate_by_gh = {}
        for gh, stats in block_stats.items():
            numerator = (
                stats["crime_weighted_decayed"] + stats["crime_shootings_weighted"]
            ) / seasonal
            units = max(float(stats["crime_units_weighted"]), 1.0)
            rate_by_gh[gh] = round(1000.0 * numerator / units, 3)

        # Absolute scoring against the frozen citywide baseline;
        # fall back to cached-block median until the first baseline build.
        # Metric: per-1000-household rate, not the raw density-confounded sum.
        per_listing = [rate_by_gh[lst["geohash"]] for lst in listings]
        scores = baseline_scores(
            conn,
            self.name,
            per_listing,
            reverse=True,
            zero_is_perfect=True,
        )
        if scores is None:
            scores = median_inverse_scores(
                per_listing, baseline=list(rate_by_gh.values())
            )

        # Score: citywide percentile (100 = 0 crime); fallback:
        # 50 = median, 100 = 0 crime, 0 = 2× median or worse
        results = []
        for lst, score in zip(listings, scores):
            gh = lst["geohash"]
            stats = block_stats[gh]
            # Trend suppression applies at emit time (cached blocks too) so
            # the cache stays valid once the feed catches up.
            if suppress_trend:
                trend_ratio, trend_direction = 1.0, "stable"
            else:
                trend_ratio = stats["crime_trend_ratio"]
                trend_direction = stats["crime_trend_direction"]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "crime_felony_count": stats["crime_felony_count"],
                        "crime_misdemeanor_count": stats["crime_misdemeanor_count"],
                        "crime_violation_count": stats["crime_violation_count"],
                        "crime_weighted_total": stats["crime_weighted_total"],
                        "crime_weighted_decayed": stats["crime_weighted_decayed"],
                        "crime_shootings_count": stats["crime_shootings_count"],
                        "crime_shootings_weighted": stats["crime_shootings_weighted"],
                        "crime_units_weighted": stats["crime_units_weighted"],
                        "crime_rate_per_khh": rate_by_gh[gh],
                        "crime_trend_ratio": trend_ratio,
                        "crime_trend_direction": trend_direction,
                    },
                )
            )
        return results
