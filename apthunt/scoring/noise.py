"""
NoiseScorer — scores listings by 311 noise-complaint density.

Uses pre-downloaded 311 Service Requests (``ds_noise``) with Haversine
circle queries cached in BlockCache by geohash.

Filters to actual noise types only:
    Noise - Residential, Noise - Street/Sidewalk, Noise - Commercial,
    Noise - Vehicle, Noise - Park.

Scoring:
    Count noise complaints within 300 m in the last 12 months, each
    weighted by recency (exponential decay, half-life 180 days) and by
    distance (Gaussian kernel, sigma = radius/2).

    The weighted sum is population-density confounded (crowded blocks
    generate more complaint *volume* at identical per-person nuisance),
    so the scored metric is a per-household rate: the weighted sum
    divided by Gaussian-kernel-weighted PLUTO residential units in the
    same radius, ×1000 (per-1000-households).  The rate is scored
    against the frozen citywide baseline (absolute); fall back to
    median-inverse until a baseline is built.
    Fewer complaints per household → higher score.

    Trend: compares recent-half (last 6 months) vs older-half
    to produce a trend ratio.  < 1.0 = improving, > 1.0 = worsening.
    If the dataset's freshest created_date lags more than
    TREND_LAG_MAX_DAYS behind today, the recent-half bucket is
    truncated by publication lag and any "improving" reading is an
    artifact — the trend is suppressed to stable for the whole batch.
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


class NoiseScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "noise"

    # Citywide-baseline metric: per-1000-household complaint rate
    # (decay- and distance-weighted sum / kernel-weighted PLUTO units)
    baseline_component = "noise_rate_per_khh"
    baseline_reverse = True
    baseline_zero_perfect = True

    # Publication-lag guard: if the dataset's newest created_date is more
    # than this many days old, the recent trend bucket is truncated and
    # the trend is suppressed (311 is fresh daily, so this should never
    # trip — kept for symmetry with lag-prone feeds like crime).
    TREND_LAG_MAX_DAYS = 30

    def columns(self) -> dict[str, str]:
        return {
            "noise_complaint_count": "INTEGER",
            "noise_weighted": "REAL",
            "noise_rate_per_khh": "REAL",
            "noise_trend_ratio": "REAL",
            "noise_trend_direction": "TEXT",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("noise", quiet=True)

        RADIUS_M = 300
        # Complaint types to count
        NOISE_TYPES = {"Noise - Residential", "Noise - Street/Sidewalk",
                       "Noise - Commercial", "Noise - Vehicle", "Noise - Park"}
        # Gaussian distance kernel: sigma = radius/2
        KERNEL_SIGMA_M = RADIUS_M / 2.0
        # Max decayed+kernel weight one location (~11m cell) may contribute —
        # a single serial complainant tops out at ~6 effective complaints/yr.
        PER_LOCATION_CAP = 6.0
        today_ord = datetime.now().toordinal()

        # Publication-lag guard (computed once per batch): if the freshest
        # record lags too far behind today, the recent-half trend bucket is
        # incomplete and "improving" would be an artifact of missing data.
        suppress_trend = True
        try:
            row = self._store.query(
                "noise", select="MAX(created_date) AS max_dt", limit=1
            )
            max_dt = (row[0].get("max_dt") or "")[:10] if row else ""
            if max_dt:
                lag_days = today_ord - datetime.fromisoformat(max_dt).toordinal()
                suppress_trend = lag_days > self.TREND_LAG_MAX_DAYS
        except (ValueError, TypeError, IndexError, sqlite3.Error):
            pass  # unparseable/missing max date → keep trend suppressed

        # Geohash to lat/lon
        geohash_to_latlon = dedupe_by_geohash(listings)
        block_stats = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            cached = self._cache.get(gh, "noise_v6")
            if cached is not None:
                block_stats[gh] = cached
                continue
            # Query local DataStore (already filtered to relevant types + last 12 months)
            rows = self._store.query_circle(
                "noise",
                lat=lat,
                lon=lon,
                radius_m=RADIUS_M,
                select="complaint_type,created_date,latitude,longitude",
            )
            noise = 0
            # Serial-complainant damping: 311 counts conflate breadth with
            # one aggrieved neighbor. Hundreds of complaints from a single
            # address (one feud, one venue) must not paint the whole block
            # "loud" — accumulate weight PER LOCATION (~11m rounding), then
            # cap each location's contribution. Widespread noise (many
            # locations) still scores loud; one loud feud does not.
            per_loc: dict = {}
            recent_count, older_count = 0, 0
            for r in rows:
                ct = r.get("complaint_type") or ""
                if ct not in NOISE_TYPES:
                    continue
                noise += 1
                # Combined weight: recency decay × Gaussian distance kernel
                dt = (r.get("created_date") or "")[:10]
                dist_m = float(r.get("_dist_m") or 0.0)
                kernel = math.exp(-((dist_m / KERNEL_SIGMA_M) ** 2))
                try:
                    loc = (round(float(r.get("latitude")), 4),
                           round(float(r.get("longitude")), 4))
                except (TypeError, ValueError):
                    loc = ("?", noise)  # unknown location: never capped together
                per_loc[loc] = per_loc.get(loc, 0.0) + kernel * decay_weight(dt, today_ord)
                # Bucket by date for trend
                if dt >= TREND_MIDPOINT:
                    recent_count += 1
                else:
                    older_count += 1
            weighted = sum(min(w, PER_LOCATION_CAP) for w in per_loc.values())

            # Trend ratio: recent / older.  < 1.0 = improving
            trend_ratio, direction = compute_trend(recent_count, older_count)

            # Per-household rate: same kernel on numerator and denominator
            # cancels the density confound (crowded ≠ noisy per household)
            units = kernel_weighted_units(self._store, lat, lon, RADIUS_M)
            rate_per_khh = round(1000.0 * weighted / units, 3)

            block_stats[gh] = {
                "noise_complaint_count": noise,
                "noise_total": noise,
                "noise_weighted": round(weighted, 3),
                "noise_rate_per_khh": rate_per_khh,
                "noise_trend_ratio": trend_ratio,
                "noise_trend_direction": direction,
            }
            self._cache.put(gh, "noise_v6", block_stats[gh])

        per_listing = [block_stats[lst["geohash"]]["noise_rate_per_khh"] for lst in listings]
        # Absolute scoring against the frozen citywide baseline
        scores = baseline_scores(
            conn, self.name, per_listing, reverse=True, zero_is_perfect=True
        )
        if scores is None:
            # Fallback until first baseline build: batch-relative median-inverse
            baseline = [v["noise_rate_per_khh"] for v in block_stats.values()]
            scores = median_inverse_scores(per_listing, baseline=baseline)

        # Score: 50 = median, 100 = 0 complaints, 0 = 2× median or worse
        results = []
        for lst, score in zip(listings, scores):
            gh = lst["geohash"]
            stats = block_stats[gh]
            # Lag suppression applied at output time (not baked into the
            # cache) so trends reappear as soon as the feed catches up.
            if suppress_trend:
                trend_ratio, trend_direction = 1.0, "stable"
            else:
                trend_ratio = stats["noise_trend_ratio"]
                trend_direction = stats["noise_trend_direction"]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "noise_complaint_count": stats["noise_complaint_count"],
                        "noise_weighted": stats["noise_weighted"],
                        "noise_rate_per_khh": stats["noise_rate_per_khh"],
                        "noise_trend_ratio": trend_ratio,
                        "noise_trend_direction": trend_direction,
                    },
                )
            )
        return results
