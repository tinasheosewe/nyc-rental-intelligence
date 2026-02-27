"""
SchoolsScorer — scores listings by public school quality nearby.

Uses the DOE High School Directory (97mf-9njv) via bounding-box query.
Quality composite = average of attendance_rate and pct_stu_safe
(both 0–1 floats).  Best school in 1.5 km wins.

Scoring: percentile-rank across all listings in the current universe.
Higher quality composite → higher score.
Listing at the median gets 50, best gets ~100, worst gets ~0.
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

        # Collect ratings for percentile ranking
        ratings: list[tuple[dict, float, str]] = []
        for lst in listings:
            stats = block_stats[lst["geohash"]]
            ratings.append((lst, stats["school_rating"], stats["school_name"]))

        # Percentile-rank: higher quality → higher score
        scores = _percentile_scores([r for _, r, _ in ratings])

        results: list[ScorerResult] = []
        for i, (lst, rating, name) in enumerate(ratings):
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=scores[i],
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


def _percentile_scores(values: list[float]) -> list[float]:
    """Convert raw values to 0–100 percentile scores (higher value → higher score)."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]

    indexed = sorted(enumerate(values), key=lambda t: t[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1

    scores = [0.0] * n
    for idx in range(n):
        pct = ranks[idx] / (n - 1) * 100.0
        scores[idx] = round(max(0.0, min(100.0, pct)), 1)
    return scores