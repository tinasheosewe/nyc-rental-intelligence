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
from datetime import date, timedelta

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult

# 6-month midpoint for trend analysis
_MIDPOINT = (date.today() - timedelta(days=182)).isoformat()


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
        geohash_to_latlon = {lst["geohash"]: (lst["lat"], lst["lon"]) for lst in listings}
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
                if dt >= _MIDPOINT:
                    recent_w += w
                else:
                    older_w += w

            weighted = fel * WEIGHTS["FELONY"] + mis * WEIGHTS["MISDEMEANOR"] + vio * WEIGHTS["VIOLATION"]

            # Trend ratio: recent / older.  < 1.0 = improving
            if older_w > 0:
                trend_ratio = round(recent_w / older_w, 3)
            elif recent_w > 0:
                trend_ratio = 2.0  # crime appeared where there was none
            else:
                trend_ratio = 1.0  # no data either half

            if trend_ratio < 0.85:
                direction = "improving"
            elif trend_ratio > 1.15:
                direction = "worsening"
            else:
                direction = "stable"

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
        all_weighted = [v["crime_weighted_total"] for v in block_stats.values()]
        if not all_weighted:
            median = 0.0
        else:
            sorted_vals = sorted(all_weighted)
            n = len(sorted_vals)
            median = (sorted_vals[n//2] if n % 2 == 1 else (sorted_vals[n//2-1] + sorted_vals[n//2]) / 2)
            if median == 0:
                median = 1.0  # avoid div0

        # Score: 50 = median, 100 = 0 crime, 0 = 2× median or worse
        results = []
        for lst in listings:
            gh = lst["geohash"]
            stats = block_stats[gh]
            w = stats["crime_weighted_total"]
            # Invert: lower crime = higher score
            if w <= median:
                score = 100.0 - 50.0 * (w / median) if median > 0 else 100.0
            else:
                score = max(0.0, 50.0 - 50.0 * ((w - median) / median))
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
