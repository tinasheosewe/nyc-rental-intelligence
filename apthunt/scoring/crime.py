"""
CrimeScorer — scores listings by crime density in their surrounding area.

Uses pre-downloaded NYPD Complaints (``ds_crime``) with Haversine
circle queries cached in BlockCache by geohash.

Scoring:
    Weight by severity: FELONY ×3, MISDEMEANOR ×1.5, VIOLATION ×1.
    Each incident additionally weighted by recency decay (half-life
    180 days) and a Gaussian distance kernel so a felony last week
    next door outweighs one 18 months ago at the radius edge.
    The decayed weighted sum is divided by kernel-weighted PLUTO
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
from apthunt.scoring.baseline import baseline_scores, decay_weight
from apthunt.scoring.utils import (
    TREND_MIDPOINT,
    compute_trend,
    dedupe_by_geohash,
    kernel_weighted_units,
    median_inverse_scores,
)


class CrimeScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "crime"

    # Baseline metric: decayed, distance-kernel-weighted severity sum per
    # 1000 kernel-weighted households.  Raw counts-in-radius are population-
    # density confounded (crowded ≠ dangerous); the per-household rate is not.
    # Lower is better; zero crime is perfect.
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

        # Get all unique geohashes for this batch
        geohash_to_latlon = dedupe_by_geohash(listings)
        # Fetch/calc for each geohash
        block_stats = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            # Try cache first (v5 key — adds per-household rate)
            cached = self._cache.get(gh, "crime_v5")
            if cached is not None:
                block_stats[gh] = cached
                continue
            # Query local DataStore — include date for trend bucketing
            rows = self._store.query_circle(
                "crime",
                lat=lat,
                lon=lon,
                radius_m=RADIUS_M,
                select="law_cat_cd,cmplnt_fr_dt",
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
                # Decayed + distance-kernel weighted contribution
                dist_m = float(r.get("_dist_m") or 0.0)
                kernel = math.exp(-((dist_m / SIGMA_M) ** 2))
                decayed += w * kernel * decay_weight(dt, today_ord)

            weighted = fel * WEIGHTS["FELONY"] + mis * WEIGHTS["MISDEMEANOR"] + vio * WEIGHTS["VIOLATION"]

            # Per-household rate: same Gaussian kernel + radius on the
            # denominator (PLUTO residential units) so density cancels.
            # ×1000 → incidents per 1000 households (human-readable).
            units = kernel_weighted_units(self._store, lat, lon, RADIUS_M)
            rate_per_khh = 1000.0 * decayed / units

            # Trend ratio: recent / older.  < 1.0 = improving
            trend_ratio, direction = compute_trend(recent_w, older_w)

            block_stats[gh] = {
                "crime_felony_count": fel,
                "crime_misdemeanor_count": mis,
                "crime_violation_count": vio,
                "crime_weighted_total": weighted,
                "crime_weighted_decayed": round(decayed, 3),
                "crime_units_weighted": round(units, 1),
                "crime_rate_per_khh": round(rate_per_khh, 3),
                "crime_trend_ratio": trend_ratio,
                "crime_trend_direction": direction,
            }
            self._cache.put(gh, "crime_v5", block_stats[gh])

        # Absolute scoring against the frozen citywide baseline;
        # fall back to cached-block median until the first baseline build.
        # Metric: per-1000-household rate, not the raw density-confounded sum.
        per_listing = [block_stats[lst["geohash"]]["crime_rate_per_khh"] for lst in listings]
        scores = baseline_scores(
            conn,
            self.name,
            per_listing,
            reverse=True,
            zero_is_perfect=True,
        )
        if scores is None:
            baseline = [v["crime_rate_per_khh"] for v in block_stats.values()]
            scores = median_inverse_scores(per_listing, baseline=baseline)

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
                        "crime_units_weighted": stats["crime_units_weighted"],
                        "crime_rate_per_khh": stats["crime_rate_per_khh"],
                        "crime_trend_ratio": trend_ratio,
                        "crime_trend_direction": trend_direction,
                    },
                )
            )
        return results
