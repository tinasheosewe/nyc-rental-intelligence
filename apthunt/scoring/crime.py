"""
CrimeScorer — scores listings by crime density in their surrounding area.

Uses pre-downloaded NYPD Complaints (``ds_crime``) with Haversine
circle queries cached in BlockCache by geohash.

Scoring:
    Weight by severity: FELONY ×3, MISDEMEANOR ×1.5, VIOLATION ×1.
    Normalize weighted count against a city-wide median.
    Invert so lower crime = higher score.

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


class CrimeScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "crime"

    def columns(self) -> dict[str, str]:
        return {
            "crime_felony_count": "INTEGER",
            "crime_misdemeanor_count": "INTEGER",
            "crime_violation_count": "INTEGER",
            "crime_weighted_total": "REAL",
            "crime_trend_ratio": "REAL",
            "crime_trend_direction": "TEXT",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("crime", quiet=True)

        # Parameters
        RADIUS_M = 400
        # Severity weights
        WEIGHTS = {"FELONY": 3.0, "MISDEMEANOR": 1.5, "VIOLATION": 1.0}
        # Get all unique geohashes for this batch
        geohash_to_latlon = dedupe_by_geohash(listings)
        # Fetch/calc for each geohash
        block_stats = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            # Try cache first (v2 key — old entries without trend data skipped)
            cached = self._cache.get(gh, "crime_v2")
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

            weighted = fel * WEIGHTS["FELONY"] + mis * WEIGHTS["MISDEMEANOR"] + vio * WEIGHTS["VIOLATION"]

            # Trend ratio: recent / older.  < 1.0 = improving
            trend_ratio, direction = compute_trend(recent_w, older_w)

            block_stats[gh] = {
                "crime_felony_count": fel,
                "crime_misdemeanor_count": mis,
                "crime_violation_count": vio,
                "crime_weighted_total": weighted,
                "crime_trend_ratio": trend_ratio,
                "crime_trend_direction": direction,
            }
            self._cache.put(gh, "crime_v2", block_stats[gh])

        # Compute citywide median for normalization (use all cached blocks)
        baseline = [v["crime_weighted_total"] for v in block_stats.values()]
        per_listing = [block_stats[lst["geohash"]]["crime_weighted_total"] for lst in listings]
        scores = median_inverse_scores(per_listing, baseline=baseline)

        # Score: 50 = median, 100 = 0 crime, 0 = 2× median or worse
        results = []
        for lst, score in zip(listings, scores):
            gh = lst["geohash"]
            stats = block_stats[gh]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "crime_felony_count": stats["crime_felony_count"],
                        "crime_misdemeanor_count": stats["crime_misdemeanor_count"],
                        "crime_violation_count": stats["crime_violation_count"],
                        "crime_weighted_total": stats["crime_weighted_total"],
                        "crime_trend_ratio": stats["crime_trend_ratio"],
                        "crime_trend_direction": stats["crime_trend_direction"],
                    },
                )
            )
        return results
