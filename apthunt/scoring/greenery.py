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

Scoring:
    canopy_points = sum(min(dbh, 36) for each tree within 200 m)
    garden_bonus  = garden_count × 20
    park_bonus    = park_count × 15
    raw = canopy_points + garden_bonus + park_bonus
    Percentile-ranked across all listings.

Output columns:
    greenery_tree_count     INTEGER — living street trees within 200 m
    greenery_canopy_score   INTEGER — diameter-weighted canopy (capped per tree)
    greenery_garden_count   INTEGER — community gardens within 500 m
    greenery_park_count     INTEGER — parks within 500 m
"""

from __future__ import annotations

import json
import sqlite3

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import dedupe_by_geohash, percentile_scores

TREE_RADIUS_M = 200
GARDEN_RADIUS_M = 500
PARK_SEARCH_DELTA = 0.005   # ~500 m in degrees


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
            cached = self._cache.get(gh, "greenery_v1")
            if cached is not None:
                block_stats[gh] = cached
                continue

            # Trees within 200 m
            tree_count, canopy = self._count_trees(lat, lon)

            # Community gardens within 500 m
            garden_count = self._count_gardens(lat, lon)

            # Parks within 500 m (count, not quality — that's ParksScorer)
            park_count = self._count_parks(lat, lon)

            raw = canopy + garden_count * 20 + park_count * 15

            stats = {
                "greenery_tree_count": tree_count,
                "greenery_canopy_score": canopy,
                "greenery_garden_count": garden_count,
                "greenery_park_count": park_count,
                "_raw": raw,
            }
            block_stats[gh] = stats
            self._cache.put(gh, "greenery_v1", stats)

        raw_values = [block_stats[lst["geohash"]]["_raw"] for lst in listings]
        pct_scores = percentile_scores(raw_values, reverse=False, zero_is_perfect=False)

        results: list[ScorerResult] = []
        for lst, pct in zip(listings, pct_scores):
            s = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=pct,
                    components={
                        "greenery_tree_count": s["greenery_tree_count"],
                        "greenery_canopy_score": s["greenery_canopy_score"],
                        "greenery_garden_count": s["greenery_garden_count"],
                        "greenery_park_count": s["greenery_park_count"],
                    },
                )
            )
        return results

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
