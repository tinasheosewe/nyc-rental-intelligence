"""
AmenityScorer — scores listings by nearby everyday amenities.

Uses pre-downloaded OpenStreetMap amenity data (via DataStore) to count
grocery stores, pharmacies, gyms, laundromats, cafés, and restaurants
within a walkable radius.

Scoring:
    Weighted amenity count within 500 m.
    Essentials (grocery, pharmacy) weighted higher than lifestyle
    (café, restaurant).  Percentile-ranked across all listings.

Output columns:
    amenity_grocery   INTEGER — supermarkets + convenience stores
    amenity_pharmacy  INTEGER
    amenity_gym       INTEGER — fitness centres
    amenity_laundry   INTEGER
    amenity_dining    INTEGER — restaurants + cafés
    amenity_total     INTEGER — weighted total
"""

from __future__ import annotations

import logging
import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult

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


class AmenityScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache
        self._store.ensure_downloaded("amenities", quiet=True)

    @property
    def name(self) -> str:
        return "amenity"

    def columns(self) -> dict[str, str]:
        return {
            "amenity_grocery": "INTEGER",
            "amenity_pharmacy": "INTEGER",
            "amenity_gym": "INTEGER",
            "amenity_laundry": "INTEGER",
            "amenity_dining": "INTEGER",
            "amenity_total": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        RADIUS_M = 500
        geohash_to_latlon = {
            lst["geohash"]: (lst["lat"], lst["lon"]) for lst in listings
        }

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            cached = self._cache.get(gh, "amenity_v2")
            if cached is not None:
                block_stats[gh] = cached
                continue

            stats = self._count_nearby(lat, lon, RADIUS_M)
            block_stats[gh] = stats
            self._cache.put(gh, "amenity_v2", stats)

        # Percentile-rank by weighted total
        raw = [block_stats[lst["geohash"]]["amenity_total"] for lst in listings]
        pct_scores = _percentile_scores(raw, reverse=False)

        results: list[ScorerResult] = []
        for lst, pct in zip(listings, pct_scores):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=pct,
                    components={
                        "amenity_grocery": stats["amenity_grocery"],
                        "amenity_pharmacy": stats["amenity_pharmacy"],
                        "amenity_gym": stats["amenity_gym"],
                        "amenity_laundry": stats["amenity_laundry"],
                        "amenity_dining": stats["amenity_dining"],
                        "amenity_total": stats["amenity_total"],
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
            "amenity_grocery": counts["grocery"],
            "amenity_pharmacy": counts["pharmacy"],
            "amenity_gym": counts["gym"],
            "amenity_laundry": counts["laundry"],
            "amenity_dining": counts["dining"],
            "amenity_total": weighted,
        }


def _percentile_scores(
    values: list[float],
    *,
    reverse: bool = False,
) -> list[float]:
    """Convert raw values to 0–100 percentile scores."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]

    indexed = sorted(enumerate(values), key=lambda t: t[1])
    scores = [0.0] * n
    for rank, (idx, _) in enumerate(indexed):
        pct = rank / (n - 1) * 100.0
        scores[idx] = (100.0 - pct) if reverse else pct
    return scores
