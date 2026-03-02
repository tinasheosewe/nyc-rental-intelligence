"""
GreeneryScorer — scores listings by surrounding green infrastructure.

Combines three data sources:

1. **Street Trees** (``ds_street_trees``) — NYC 2015 Street Tree Census.
   Counts living trees within 200 m and computes a canopy score based
   on trunk diameter (larger trees = more canopy).

2. **Community Gardens** (``ds_community_gardens``) — GreenThumb gardens.
   Counts community gardens within 500 m.

3. **Parks** (``ds_parks``) — reuses the same dataset as ParksScorer
   to count parks within 500 m (complements the parks *score* which
   only tracks the single best park).

Scoring (absolute, 0–100):
    Each component is scored independently with a sqrt diminishing-returns
    curve and capped at a threshold representing "excellent" urban greenery:

        tree_pts   = sqrt(min(1, trees  / 200))  × 30   (200 trees = full)
        canopy_pts = sqrt(min(1, canopy / 2000)) × 30   (2000 = mature canopy)
        park_pts   = sqrt(min(1, parks  / 10))   × 25   (10 parks = full)
        garden_pts = sqrt(min(1, gardens / 3))   × 15   (3 gardens = full)

        score = tree_pts + canopy_pts + park_pts + garden_pts   (max 100)

Output columns:
    greenery_tree_count     INTEGER — living street trees within 200 m
    greenery_canopy_score   INTEGER — diameter-weighted canopy (capped per tree)
    greenery_garden_count   INTEGER — community gardens within 500 m
    greenery_park_count     INTEGER — parks within 500 m
"""

from __future__ import annotations

import json
import math
import sqlite3

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import dedupe_by_geohash

TREE_RADIUS_M = 200
GARDEN_RADIUS_M = 500
PARK_SEARCH_DELTA = 0.005   # ~500 m in degrees

# Absolute-score thresholds (component reaches full marks at this value)
_TREE_CAP = 200       # street trees within 200 m
_CANOPY_CAP = 2000    # diameter-weighted canopy sum
_PARK_CAP = 10        # parks within 500 m
_GARDEN_CAP = 3       # community gardens within 500 m

# Component weights (sum = 100)
_W_TREE = 30
_W_CANOPY = 30
_W_PARK = 25
_W_GARDEN = 15


class GreeneryScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "greenery"

    def columns(self) -> dict[str, str]:
        return {
            "greenery_tree_count": "INTEGER",
            "greenery_canopy_score": "INTEGER",
            "greenery_garden_count": "INTEGER",
            "greenery_park_count": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("street_trees", quiet=True)
        self._store.ensure_downloaded("community_gardens", quiet=True)
        self._store.ensure_downloaded("parks", quiet=True)

        gh_map = dedupe_by_geohash(listings)

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "greenery_v2")
            if cached is not None:
                block_stats[gh] = cached
                continue

            # Trees within 200 m
            tree_count, canopy = self._count_trees(lat, lon)

            # Community gardens within 500 m
            garden_count = self._count_gardens(lat, lon)

            # Parks within 500 m (count, not quality — that's ParksScorer)
            park_count = self._count_parks(lat, lon)

            stats = {
                "greenery_tree_count": tree_count,
                "greenery_canopy_score": canopy,
                "greenery_garden_count": garden_count,
                "greenery_park_count": park_count,
            }
            block_stats[gh] = stats
            self._cache.put(gh, "greenery_v2", stats)

        results: list[ScorerResult] = []
        for lst in listings:
            s = block_stats[lst["geohash"]]
            score = self._absolute_score(
                s["greenery_tree_count"],
                s["greenery_canopy_score"],
                s["greenery_garden_count"],
                s["greenery_park_count"],
            )
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "greenery_tree_count": s["greenery_tree_count"],
                        "greenery_canopy_score": s["greenery_canopy_score"],
                        "greenery_garden_count": s["greenery_garden_count"],
                        "greenery_park_count": s["greenery_park_count"],
                    },
                )
            )
        return results

    @staticmethod
    def _absolute_score(
        trees: int, canopy: int, gardens: int, parks: int,
    ) -> float:
        """Compute an absolute 0-100 greenery score with sqrt diminishing returns."""
        tree_pts = math.sqrt(min(1.0, trees / _TREE_CAP)) * _W_TREE
        canopy_pts = math.sqrt(min(1.0, canopy / _CANOPY_CAP)) * _W_CANOPY
        park_pts = math.sqrt(min(1.0, parks / _PARK_CAP)) * _W_PARK
        garden_pts = math.sqrt(min(1.0, gardens / _GARDEN_CAP)) * _W_GARDEN
        return round(tree_pts + canopy_pts + park_pts + garden_pts, 1)

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _count_trees(self, lat: float, lon: float) -> tuple[int, int]:
        """Count trees within TREE_RADIUS_M and sum diameter-weighted canopy."""
        rows = self._store.query_circle(
            "street_trees",
            lat=lat,
            lon=lon,
            radius_m=TREE_RADIUS_M,
            select="tree_dbh",
        )
        count = len(rows)
        canopy = 0
        for r in rows:
            try:
                dbh = min(int(float(r.get("tree_dbh") or 0)), 36)
            except (ValueError, TypeError):
                dbh = 0
            canopy += dbh
        return count, canopy

    def _count_gardens(self, lat: float, lon: float) -> int:
        """Count community gardens within GARDEN_RADIUS_M."""
        rows = self._store.query_circle(
            "community_gardens",
            lat=lat,
            lon=lon,
            radius_m=GARDEN_RADIUS_M,
            select="garden_name",
        )
        return len(rows)

    def _count_parks(self, lat: float, lon: float) -> int:
        """Count park polygons whose centroid falls within ~500 m."""
        rows = self._store.query_bbox(
            "parks",
            lat,
            lon,
            delta=PARK_SEARCH_DELTA,
            select="name311",
            lat_col="centroid_lat",
            lon_col="centroid_lon",
        )
        return len(rows)
