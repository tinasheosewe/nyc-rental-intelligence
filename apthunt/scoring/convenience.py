"""
ConvenienceScorer — scores listings by nearby everyday conveniences.

Uses pre-downloaded OpenStreetMap amenity data (via DataStore) to count
grocery stores, pharmacies, gyms, laundromats, cafés, and restaurants
within a walkable radius.

Scoring (citywide baseline, 0–100):
    Each amenity is weighted by a Gaussian distance kernel
    exp(-(d / (radius/2))²) so a grocery at the corner counts more than
    one at the edge of the radius.  The kernel-weighted per-category sums
    feed the sqrt diminishing-returns formula:

        grocery_pts  = sqrt(min(1, grocery / 8))  × 30
        pharmacy_pts = sqrt(min(1, pharmacy / 4)) × 20
        dining_pts   = sqrt(min(1, dining / 30))  × 20
        gym_pts      = sqrt(min(1, gym / 4))      × 15
        laundry_pts  = sqrt(min(1, laundry / 3))  × 15

        convenience_weighted = grocery_pts + ... + laundry_pts

    That raw metric is then mapped to its citywide percentile via
    baseline_scores (more amenities = better).  Until the first baseline
    build it falls back to the raw formula applied to unweighted counts.

Output columns:
    convenience_grocery   INTEGER — supermarkets + convenience stores
    convenience_pharmacy  INTEGER
    convenience_gym       INTEGER — fitness centres
    convenience_laundry   INTEGER
    convenience_dining    INTEGER — restaurants + cafés
    convenience_total     INTEGER — weighted total (legacy, kept for reference)
    convenience_weighted  REAL    — kernel-weighted sqrt-cap metric (drives score)
"""

from __future__ import annotations

import logging
import math
import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores
from apthunt.scoring.utils import dedupe_by_geohash

log = logging.getLogger(__name__)

# Category weights for weighted total (kept for display)
_WEIGHTS = {
    "grocery": 3,
    "pharmacy": 3,
    "gym": 2,
    "laundry": 2,
    "dining": 1,
}

# Absolute-score thresholds and weights (sum = 100)
_CAPS = {
    "grocery": 8,
    "pharmacy": 4,
    "gym": 4,
    "laundry": 3,
    "dining": 30,
}
_SCORE_WEIGHTS = {
    "grocery": 30,
    "pharmacy": 20,
    "dining": 20,
    "gym": 15,
    "laundry": 15,
}

_CATEGORIES = list(_WEIGHTS.keys())


class ConvenienceScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache
        self._store.ensure_downloaded("amenities", quiet=True)

    @property
    def name(self) -> str:
        return "convenience"

    # Citywide baseline declaration (sampled by scripts/build_baseline.py)
    baseline_component = "convenience_weighted"
    baseline_reverse = False        # more amenities = better
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "convenience_grocery": "INTEGER",
            "convenience_pharmacy": "INTEGER",
            "convenience_gym": "INTEGER",
            "convenience_laundry": "INTEGER",
            "convenience_dining": "INTEGER",
            "convenience_total": "INTEGER",
            "convenience_weighted": "REAL",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        RADIUS_M = 500
        geohash_to_latlon = dedupe_by_geohash(listings)

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            cached = self._cache.get(gh, "convenience_v3")
            if cached is not None:
                block_stats[gh] = cached
                continue

            stats = self._count_nearby(lat, lon, RADIUS_M)
            block_stats[gh] = stats
            self._cache.put(gh, "convenience_v3", stats)

        # Absolute scoring against the frozen citywide distribution of the
        # kernel-weighted metric; fall back to the raw formula until the
        # first baseline build.
        raw_values = [
            block_stats[lst["geohash"]]["convenience_weighted"] for lst in listings
        ]
        scores = baseline_scores(
            conn, self.name, raw_values,
            reverse=self.baseline_reverse,
            zero_is_perfect=self.baseline_zero_perfect,
        )
        if scores is None:
            scores = [
                self._absolute_score(block_stats[lst["geohash"]])
                for lst in listings
            ]

        results: list[ScorerResult] = []
        for lst, score in zip(listings, scores):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "convenience_grocery": stats["convenience_grocery"],
                        "convenience_pharmacy": stats["convenience_pharmacy"],
                        "convenience_gym": stats["convenience_gym"],
                        "convenience_laundry": stats["convenience_laundry"],
                        "convenience_dining": stats["convenience_dining"],
                        "convenience_total": stats["convenience_total"],
                        "convenience_weighted": stats["convenience_weighted"],
                    },
                )
            )
        return results

    @staticmethod
    def _absolute_score(stats: dict) -> float:
        """Compute an absolute 0-100 convenience score with sqrt diminishing returns."""
        total = 0.0
        for cat in _CATEGORIES:
            count = stats[f"convenience_{cat}"]
            cap = _CAPS[cat]
            weight = _SCORE_WEIGHTS[cat]
            total += math.sqrt(min(1.0, count / cap)) * weight
        return round(total, 1)

    @staticmethod
    def _weighted_metric(kernel_counts: dict) -> float:
        """Sqrt-cap formula applied to kernel-weighted category sums."""
        total = 0.0
        for cat in _CATEGORIES:
            total += math.sqrt(min(1.0, kernel_counts[cat] / _CAPS[cat])) * _SCORE_WEIGHTS[cat]
        return round(total, 2)

    # ------------------------------------------------------------------

    def _count_nearby(self, lat: float, lon: float, radius_m: int) -> dict:
        """Count amenities within radius_m of (lat, lon) using local data."""
        nearby = self._store.query_circle(
            "amenities", lat, lon, radius_m,
            select="category",
            lat_col="lat", lon_col="lon",
        )

        # Gaussian distance kernel: full weight at the doorstep, ~0.37 at
        # radius/2, ~0.02 at the edge of the radius.
        sigma = radius_m / 2.0
        counts = {cat: 0 for cat in _CATEGORIES}
        kernel_counts = {cat: 0.0 for cat in _CATEGORIES}
        for row in nearby:
            cat = row.get("category", "")
            if cat not in counts:
                continue
            counts[cat] += 1
            d = row.get("_dist_m", 0.0) or 0.0
            kernel_counts[cat] += math.exp(-((d / sigma) ** 2))

        weighted = sum(counts[cat] * _WEIGHTS[cat] for cat in _CATEGORIES)

        return {
            "convenience_grocery": counts["grocery"],
            "convenience_pharmacy": counts["pharmacy"],
            "convenience_gym": counts["gym"],
            "convenience_laundry": counts["laundry"],
            "convenience_dining": counts["dining"],
            "convenience_total": weighted,
            "convenience_weighted": self._weighted_metric(kernel_counts),
        }
