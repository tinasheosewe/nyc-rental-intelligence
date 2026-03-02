"""
ConvenienceScorer — scores listings by nearby everyday conveniences.

Uses pre-downloaded OpenStreetMap amenity data (via DataStore) to count
grocery stores, pharmacies, gyms, laundromats, cafés, and restaurants
within a walkable radius.

Scoring:
    Weighted convenience count within 500 m.
    Essentials (grocery, pharmacy) weighted higher than lifestyle
    (café, restaurant).  Percentile-ranked across all listings.

Output columns:
    convenience_grocery   INTEGER — supermarkets + convenience stores
    convenience_pharmacy  INTEGER
    convenience_gym       INTEGER — fitness centres
    convenience_laundry   INTEGER
    convenience_dining    INTEGER — restaurants + cafés
    convenience_total     INTEGER — weighted total
"""

from __future__ import annotations

import logging
import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import dedupe_by_geohash, percentile_scores

log = logging.getLogger(__name__)

# Category weights (essentials > lifestyle)
_WEIGHTS = {
    "grocery": 3,
    "pharmacy": 3,
    "gym": 2,
    "laundry": 2,
    "dining": 1,
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

    def columns(self) -> dict[str, str]:
        return {
            "convenience_grocery": "INTEGER",
            "convenience_pharmacy": "INTEGER",
            "convenience_gym": "INTEGER",
            "convenience_laundry": "INTEGER",
            "convenience_dining": "INTEGER",
            "convenience_total": "INTEGER",
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
            cached = self._cache.get(gh, "convenience_v2")
            if cached is not None:
                block_stats[gh] = cached
                continue

            stats = self._count_nearby(lat, lon, RADIUS_M)
            block_stats[gh] = stats
            self._cache.put(gh, "convenience_v2", stats)

        # Percentile-rank by weighted total
        raw = [block_stats[lst["geohash"]]["convenience_total"] for lst in listings]
        pct_scores = percentile_scores(raw, reverse=False)

        results: list[ScorerResult] = []
        for lst, pct in zip(listings, pct_scores):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=pct,
                    components={
                        "convenience_grocery": stats["convenience_grocery"],
                        "convenience_pharmacy": stats["convenience_pharmacy"],
                        "convenience_gym": stats["convenience_gym"],
                        "convenience_laundry": stats["convenience_laundry"],
                        "convenience_dining": stats["convenience_dining"],
                        "convenience_total": stats["convenience_total"],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------

    def _count_nearby(self, lat: float, lon: float, radius_m: int) -> dict:
        """Count amenities within radius_m of (lat, lon) using local data."""
        nearby = self._store.query_circle(
            "amenities", lat, lon, radius_m,
            select="category",
            lat_col="lat", lon_col="lon",
        )

        counts = {cat: 0 for cat in _CATEGORIES}
        for row in nearby:
            cat = row.get("category", "")
            if cat in counts:
                counts[cat] += 1

        weighted = sum(counts[cat] * _WEIGHTS[cat] for cat in _CATEGORIES)

        return {
            "convenience_grocery": counts["grocery"],
            "convenience_pharmacy": counts["pharmacy"],
            "convenience_gym": counts["gym"],
            "convenience_laundry": counts["laundry"],
            "convenience_dining": counts["dining"],
            "convenience_total": weighted,
        }
