"""
SchoolsScorer — scores listings by public school quality nearby.

Uses the DOE High School Directory (97mf-9njv) via bounding-box query.
Quality composite = average of attendance_rate and pct_stu_safe
(both 0–1 floats).  Best school in 1.5 km wins.

Scoring (quality × 100):
    ≥ 90 → 100
    ≥ 80 →  80
    ≥ 70 →  60
    ≥ 60 →  40
    < 60 →  20
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.soda_client import SodaClient
from apthunt.scoring.base import Scorer, ScorerResult

# 0.014° ≈ 1.5 km at NYC latitude
_BBOX_DELTA = 0.014

_SCHOOL_DATASET = "97mf-9njv"

_SELECT = "school_name, latitude, longitude, attendance_rate, pct_stu_safe"


class SchoolsScorer(Scorer):

    def __init__(self, soda: SodaClient, cache: BlockCache):
        self._soda = soda
        self._cache = cache

    @property
    def name(self) -> str:
        return "schools"

    def columns(self) -> dict[str, str]:
        return {
            "school_name": "TEXT",
            "school_rating": "REAL",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        # Deduplicate by geohash
        gh_map: dict[str, tuple[float, float]] = {}
        for lst in listings:
            gh_map.setdefault(lst["geohash"], (lst["lat"], lst["lon"]))

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "schools")
            if cached is not None:
                block_stats[gh] = cached
                continue

            schools = self._fetch_schools(lat, lon)
            rating, name = self._best_school(schools)
            stats = {"school_rating": rating, "school_name": name}
            block_stats[gh] = stats
            self._cache.put(gh, "schools", stats)

        results: list[ScorerResult] = []
        for lst in listings:
            stats = block_stats[lst["geohash"]]
            rating = stats["school_rating"]
            name = stats["school_name"]

            if rating >= 90:
                sc = 100.0
            elif rating >= 80:
                sc = 80.0
            elif rating >= 70:
                sc = 60.0
            elif rating >= 60:
                sc = 40.0
            else:
                sc = 20.0

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=sc,
                    components={
                        "school_name": name,
                        "school_rating": rating,
                    },
                )
            )
        return results

    # ------------------------------------------------------------------

    def _fetch_schools(self, lat: float, lon: float) -> list[dict]:
        """Fetch schools in a ~1.5 km bbox via latitude/longitude columns."""
        return self._soda.query_bbox(
            dataset=_SCHOOL_DATASET,
            lat_col="latitude",
            lon_col="longitude",
            min_lat=lat - _BBOX_DELTA,
            max_lat=lat + _BBOX_DELTA,
            min_lon=lon - _BBOX_DELTA,
            max_lon=lon + _BBOX_DELTA,
            select=_SELECT,
        )

    @staticmethod
    def _best_school(schools: list[dict]) -> tuple[float, str]:
        """Return (rating, name) for the best school by quality composite.

        Quality = mean(attendance_rate, pct_stu_safe) × 100.
        """
        best_rating = 0.0
        best_name = ""

        for s in schools:
            try:
                att = float(s.get("attendance_rate") or 0)
            except (ValueError, TypeError):
                att = 0.0
            try:
                safe = float(s.get("pct_stu_safe") or 0)
            except (ValueError, TypeError):
                safe = 0.0

            # Both are 0-1 floats (e.g. 0.97)
            parts = [v for v in (att, safe) if v > 0]
            if not parts:
                continue
            quality = (sum(parts) / len(parts)) * 100.0

            if quality > best_rating:
                best_rating = round(quality, 1)
                best_name = s.get("school_name", "")

        return best_rating, best_name
