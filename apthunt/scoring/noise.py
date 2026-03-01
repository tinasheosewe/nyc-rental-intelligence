"""
NoiseScorer — scores listings by 311 quality-of-life complaints density.

Uses pre-downloaded 311 Service Requests (``ds_noise``) with Haversine
circle queries cached in BlockCache by geohash.

Filters to: Noise - Residential, Noise - Street/Sidewalk, Rodent,
HEAT/HOT WATER, etc.

Scoring:
    Count relevant complaints within 300m in the last 12 months.
    Normalize against city baseline.
    Invert so fewer complaints = higher score.

    Trend: compares recent-half (last 6 months) vs older-half
    to produce a trend ratio.  < 1.0 = improving, > 1.0 = worsening.
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import (
    TREND_MIDPOINT,
    compute_trend,
    dedupe_by_geohash,
    median_inverse_scores,
)


class NoiseScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "noise"

    def columns(self) -> dict[str, str]:
        return {
            "noise_complaint_count": "INTEGER",
            "noise_rodent_count": "INTEGER",
            "noise_heat_count": "INTEGER",
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
        RODENT = "Rodent"
        HEAT = "HEAT/HOT WATER"
        # Geohash to lat/lon
        geohash_to_latlon = dedupe_by_geohash(listings)
        block_stats = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            cached = self._cache.get(gh, "311_v2")
            if cached is not None:
                block_stats[gh] = cached
                continue
            # Query local DataStore (already filtered to relevant types + last 12 months)
            rows = self._store.query_circle(
                "noise",
                lat=lat,
                lon=lon,
                radius_m=RADIUS_M,
                select="complaint_type,created_date",
            )
            noise, rodent, heat = 0, 0, 0
            recent_count, older_count = 0, 0
            for r in rows:
                ct = r.get("complaint_type") or ""
                if ct in NOISE_TYPES:
                    noise += 1
                elif ct == RODENT:
                    rodent += 1
                elif ct == HEAT:
                    heat += 1
                else:
                    continue
                # Bucket by date for trend
                dt = (r.get("created_date") or "")[:10]
                if dt >= TREND_MIDPOINT:
                    recent_count += 1
                else:
                    older_count += 1

            total = noise + rodent + heat

            # Trend ratio: recent / older.  < 1.0 = improving
            trend_ratio, direction = compute_trend(recent_count, older_count)

            block_stats[gh] = {
                "noise_complaint_count": noise,
                "noise_rodent_count": rodent,
                "noise_heat_count": heat,
                "noise_total": total,
                "noise_trend_ratio": trend_ratio,
                "noise_trend_direction": direction,
            }
            self._cache.put(gh, "311_v2", block_stats[gh])

        baseline = [v["noise_total"] for v in block_stats.values()]
        per_listing = [block_stats[lst["geohash"]]["noise_total"] for lst in listings]
        scores = median_inverse_scores(per_listing, baseline=baseline)

        # Score: 50 = median, 100 = 0 complaints, 0 = 2× median or worse
        results = []
        for lst, score in zip(listings, scores):
            gh = lst["geohash"]
            stats = block_stats[gh]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "noise_complaint_count": stats["noise_complaint_count"],
                        "noise_rodent_count": stats["noise_rodent_count"],
                        "noise_heat_count": stats["noise_heat_count"],
                        "noise_trend_ratio": stats["noise_trend_ratio"],
                        "noise_trend_direction": stats["noise_trend_direction"],
                    },
                )
            )
        return results
