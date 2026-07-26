"""
SchoolsScorer — scores listings by public school quality nearby.

Uses pre-downloaded DOE High School Directory (stored in ``ds_schools``).
Quality composite = average of attendance_rate and pct_stu_safe
(both 0–1 floats).  Best school in 1.5 km wins.

Scoring (absolute, 0–100):
    Raw metric = quality composite of best nearby school × 100
    (attendance rate and student safety percentage), stored unchanged
    in components as ``school_rating``.  The final score maps that
    composite onto the frozen citywide baseline distribution
    (``baseline_scores``) so spread is citywide-calibrated; until the
    first baseline build, the raw composite itself is the score.
    Higher composite → higher score.  No school nearby → 0.
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores
from apthunt.scoring.utils import dedupe_by_geohash

# 0.014° ≈ 1.5 km at NYC latitude
_BBOX_DELTA = 0.014


class SchoolsScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "schools"

    # Raw metric sampled citywide by scripts/build_baseline.py
    baseline_component = "school_rating"
    baseline_reverse = False        # higher composite = better
    baseline_zero_perfect = False   # no school nearby (0) is worst, not best

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
        self._store.ensure_downloaded("schools", quiet=True)

        # Deduplicate by geohash
        gh_map = dedupe_by_geohash(listings)

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

        # Absolute scoring: map the raw composite onto the citywide
        # baseline distribution; fall back to the raw composite (already
        # a meaningful 0-100 scale) until the first baseline build.
        raw_values = [block_stats[lst["geohash"]]["school_rating"] for lst in listings]
        scores = baseline_scores(
            conn, self.name, raw_values,
            reverse=self.baseline_reverse,
            zero_is_perfect=self.baseline_zero_perfect,
        )
        if scores is None:
            scores = raw_values

        results: list[ScorerResult] = []
        for lst, sc in zip(listings, scores):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=sc,
                    components={
                        "school_name": stats["school_name"],
                        "school_rating": stats["school_rating"],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------

    def _fetch_schools(self, lat: float, lon: float) -> list[dict]:
        """Fetch schools in a ~1.5 km bbox from local data."""
        return self._store.query_bbox(
            "schools", lat, lon, delta=_BBOX_DELTA,
            select="school_name, latitude, longitude, attendance_rate, pct_stu_safe",
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