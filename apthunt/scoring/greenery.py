"""
GreeneryScorer — scores listings by surrounding green infrastructure.

Measures *street-level* greenery — distinct from the Parks dimension which
scores proximity to a single best park.  Greenery captures the density of
living trees, mature canopy cover, and community gardens around a listing.

Data sources:

1. **Street Trees** (``ds_street_trees``) — NYC 2015 Street Tree Census.
   Counts living trees within 200 m and computes a canopy score based
   on trunk diameter (larger trees = more canopy).

2. **Community Gardens** (``ds_community_gardens``) — GreenThumb gardens.
   Counts community gardens within 500 m.

Scoring (absolute, 0–100):
    Each component is scored independently with a sqrt diminishing-returns
    curve and capped at a threshold representing "excellent" urban greenery:

        tree_pts   = sqrt(min(1, trees  / 200))  × 40   (200 trees = full)
        canopy_pts = sqrt(min(1, canopy / 2000)) × 40   (2000 = mature canopy)
        garden_pts = sqrt(min(1, gardens / 3))   × 20   (3 gardens = full)

        score = tree_pts + canopy_pts + garden_pts   (max 100)

Note: ``greenery_park_count`` is still collected and stored for display
purposes but does NOT contribute to the score — park quality is handled
exclusively by the Parks dimension.

Output columns:
    greenery_tree_count     INTEGER — living street trees within 200 m
    greenery_canopy_score   INTEGER — diameter-weighted canopy (capped per tree)
    greenery_garden_count   INTEGER — community gardens within 500 m
    greenery_park_count     INTEGER — parks within 500 m (informational only)
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
_GARDEN_CAP = 3       # community gardens within 500 m

# Component weights (sum = 100)
_W_TREE = 40
_W_CANOPY = 40
_W_GARDEN = 20


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
        trees: int, canopy: int, gardens: int,
    ) -> float:
        """Compute an absolute 0-100 greenery score with sqrt diminishing returns.

        Parks are intentionally excluded — that's a separate dimension.
        """
        tree_pts = math.sqrt(min(1.0, trees / _TREE_CAP)) * _W_TREE
        canopy_pts = math.sqrt(min(1.0, canopy / _CANOPY_CAP)) * _W_CANOPY
        garden_pts = math.sqrt(min(1.0, gardens / _GARDEN_CAP)) * _W_GARDEN
        return round(tree_pts + canopy_pts + garden_pts, 1)

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
