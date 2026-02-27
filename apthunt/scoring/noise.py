"""
NoiseScorer — scores listings by 311 quality-of-life complaints density.

Uses 311 Service Requests via SODA API with within_circle() queries
cached in BlockCache by geohash.

Filters to: Noise - Residential, Noise - Street/Sidewalk, Rodent,
HEAT/HOT WATER, Unsanitary Condition.

Scoring:
    Count relevant complaints within 300m in the last 12 months.
    Normalize against city baseline.
    Invert so fewer complaints = higher score.

Status: STUB — implementation pending.
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.soda_client import SodaClient
from apthunt.scoring.base import Scorer, ScorerResult


class NoiseScorer(Scorer):

    def __init__(self, soda: SodaClient, cache: BlockCache):
        self._soda = soda
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
        RADIUS_M = 300
        # Complaint types to count
        NOISE_TYPES = ["Noise - Residential", "Noise - Street/Sidewalk"]
        # Other quality-of-life
        RODENT = "Rodent"
        HEAT = "HEAT/HOT WATER"
        # Date filter: last 12 months
        from datetime import date, timedelta
        since = (date.today() - timedelta(days=365)).isoformat()
        # Geohash to lat/lon
        geohash_to_latlon = {lst["geohash"]: (lst["lat"], lst["lon"]) for lst in listings}
        block_stats = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            cached = self._cache.get(gh, "311")
            if cached is not None:
                block_stats[gh] = cached
                continue
            # Query SODA for this block
            extra_where = (
                f"created_date > '{since}' AND (complaint_type in ('Noise - Residential','Noise - Street/Sidewalk','Rodent','HEAT/HOT WATER'))"
            )
            rows = self._soda.query_circle(
                dataset="311",
                geo_column="location",
                lat=lat,
                lon=lon,
                radius_m=RADIUS_M,
                select="complaint_type",
                extra_where=extra_where,
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
