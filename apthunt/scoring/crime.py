"""
CrimeScorer — scores listings by crime density in their surrounding area.

Uses pre-downloaded NYPD Complaints (``ds_crime``) with Haversine
circle queries cached in BlockCache by geohash.

Scoring:
    Weight by severity: FELONY ×3, MISDEMEANOR ×1.5, VIOLATION ×1.
    Normalize weighted count against a city-wide median.
    Invert so lower crime = higher score.
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult


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
            # Try cache first
            cached = self._cache.get(gh, "crime")
            if cached is not None:
                block_stats[gh] = cached
                continue
            # Query local DataStore (already filtered to last 12 months)
            rows = self._store.query_circle(
                "crime",
                lat=lat,
                lon=lon,
                radius_m=RADIUS_M,
                select="law_cat_cd",
            )
            # Count by severity
            fel, mis, vio = 0, 0, 0
            for r in rows:
                cat = (r.get("law_cat_cd") or "").upper()
                if cat == "FELONY":
                    fel += 1
                elif cat == "MISDEMEANOR":
                    mis += 1
                elif cat == "VIOLATION":
                    vio += 1
            weighted = fel * WEIGHTS["FELONY"] + mis * WEIGHTS["MISDEMEANOR"] + vio * WEIGHTS["VIOLATION"]
            block_stats[gh] = {
                "crime_felony_count": fel,
                "crime_misdemeanor_count": mis,
                "crime_violation_count": vio,
                "crime_weighted_total": weighted,
            }
            self._cache.put(gh, "crime", block_stats[gh])

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
                    },
                )
            )
        return results
