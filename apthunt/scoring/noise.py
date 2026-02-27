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
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult


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
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("noise", quiet=True)

        RADIUS_M = 300
        # Complaint types to count
        NOISE_TYPES = ["Noise - Residential", "Noise - Street/Sidewalk"]
        # Other quality-of-life
        RODENT = "Rodent"
        HEAT = "HEAT/HOT WATER"
        # Geohash to lat/lon
        geohash_to_latlon = {lst["geohash"]: (lst["lat"], lst["lon"]) for lst in listings}
        block_stats = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            cached = self._cache.get(gh, "311")
            if cached is not None:
                block_stats[gh] = cached
                continue
            # Query local DataStore (already filtered to relevant types + last 12 months)
            rows = self._store.query_circle(
                "noise",
                lat=lat,
                lon=lon,
                radius_m=RADIUS_M,
                select="complaint_type",
            )
            noise = sum(1 for r in rows if r.get("complaint_type") in NOISE_TYPES)
            rodent = sum(1 for r in rows if r.get("complaint_type") == RODENT)
            heat = sum(1 for r in rows if r.get("complaint_type") == HEAT)
            total = noise + rodent + heat
            block_stats[gh] = {
                "noise_complaint_count": noise,
                "noise_rodent_count": rodent,
                "noise_heat_count": heat,
                "noise_total": total,
            }
            self._cache.put(gh, "311", block_stats[gh])

        all_total = [v["noise_total"] for v in block_stats.values()]
        if not all_total:
            median = 0.0
        else:
            sorted_vals = sorted(all_total)
            n = len(sorted_vals)
            median = (sorted_vals[n//2] if n % 2 == 1 else (sorted_vals[n//2-1] + sorted_vals[n//2]) / 2)
            if median == 0:
                median = 1.0

        # Score: 50 = median, 100 = 0 complaints, 0 = 2× median or worse
        results = []
        for lst in listings:
            gh = lst["geohash"]
            stats = block_stats[gh]
            t = stats["noise_total"]
            if t <= median:
                score = 100.0 - 50.0 * (t / median) if median > 0 else 100.0
            else:
                score = max(0.0, 50.0 - 50.0 * ((t - median) / median))
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "noise_complaint_count": stats["noise_complaint_count"],
                        "noise_rodent_count": stats["noise_rodent_count"],
                        "noise_heat_count": stats["noise_heat_count"],
                    },
                )
            )
        return results
