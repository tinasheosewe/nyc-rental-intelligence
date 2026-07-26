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

Scoring (citywide baseline, 0–100):
    Trees and gardens are additionally distance-weighted with a Gaussian
    kernel exp(-(d/(radius/2))²) so a tree at the doorstep counts more than
    one at the edge of the circle.  The kernel-weighted counts combine into
    a single raw greenery metric (uncapped, normalized weighted sum):

        greenery_weighted = trees_w/200 × 40 + canopy_w/2000 × 40
                            + gardens_w/3 × 20

    which is scored against the frozen citywide distribution via
    ``baseline_scores`` (more greenery = better).  Until the first
    ``build_baseline.py`` run, falls back to the legacy absolute formula:

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
    greenery_weighted       REAL    — kernel-weighted combined raw metric
"""

from __future__ import annotations

import json
import math
import sqlite3

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores
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

    # Citywide-baseline declaration (sampled by scripts/build_baseline.py)
    baseline_component = "greenery_weighted"
    baseline_reverse = False          # more greenery = better
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "greenery_tree_count": "INTEGER",
            "greenery_canopy_score": "INTEGER",
            "greenery_garden_count": "INTEGER",
            "greenery_park_count": "INTEGER",
            "greenery_weighted": "REAL",
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
            cached = self._cache.get(gh, "greenery_v3")
            if cached is not None:
                block_stats[gh] = cached
                continue

            # Trees within 200 m (plain counts + Gaussian kernel-weighted)
            tree_count, canopy, tree_w, canopy_w = self._count_trees(lat, lon)

            # Community gardens within 500 m (plain + kernel-weighted)
            garden_count, garden_w = self._count_gardens(lat, lon)

            # Parks within 500 m (count, not quality — that's ParksScorer)
            park_count = self._count_parks(lat, lon)

            stats = {
                "greenery_tree_count": tree_count,
                "greenery_canopy_score": canopy,
                "greenery_garden_count": garden_count,
                "greenery_park_count": park_count,
                "greenery_weighted": self._weighted_metric(
                    tree_w, canopy_w, garden_w
                ),
            }
            block_stats[gh] = stats
            self._cache.put(gh, "greenery_v3", stats)

        raw_values = [
            block_stats[lst["geohash"]]["greenery_weighted"] for lst in listings
        ]
        scores = baseline_scores(
            conn, self.name, raw_values, reverse=False, zero_is_perfect=False
        )
        if scores is None:
            # Fallback until the first citywide baseline build
            scores = [
                self._absolute_score(
                    block_stats[lst["geohash"]]["greenery_tree_count"],
                    block_stats[lst["geohash"]]["greenery_canopy_score"],
                    block_stats[lst["geohash"]]["greenery_garden_count"],
                )
                for lst in listings
            ]

        results: list[ScorerResult] = []
        for lst, score in zip(listings, scores):
            s = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "greenery_tree_count": s["greenery_tree_count"],
                        "greenery_canopy_score": s["greenery_canopy_score"],
                        "greenery_garden_count": s["greenery_garden_count"],
                        "greenery_park_count": s["greenery_park_count"],
                        "greenery_weighted": s["greenery_weighted"],
                    },
                )
            )
        return results

    @staticmethod
    def _weighted_metric(tree_w: float, canopy_w: float, garden_w: float) -> float:
        """Uncapped normalized weighted sum of kernel-weighted counts.

        Monotone in greenery; scored against the citywide baseline (no
        saturation caps needed since percentile mapping handles scale).
        """
        return round(
            tree_w / _TREE_CAP * _W_TREE
            + canopy_w / _CANOPY_CAP * _W_CANOPY
            + garden_w / _GARDEN_CAP * _W_GARDEN,
            3,
        )

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

    @staticmethod
    def _kernel(dist_m: float, radius_m: float) -> float:
        """Gaussian distance kernel: full weight at the doorstep, ~0.02 at
        the circle's edge."""
        return math.exp(-((dist_m / (radius_m / 2.0)) ** 2))

    def _count_trees(self, lat: float, lon: float) -> tuple[int, int, float, float]:
        """Count trees within TREE_RADIUS_M and sum diameter-weighted canopy.

        Returns (count, canopy, kernel-weighted count, kernel-weighted canopy).
        """
        rows = self._store.query_circle(
            "street_trees",
            lat=lat,
            lon=lon,
            radius_m=TREE_RADIUS_M,
            select="tree_dbh",
        )
        count = len(rows)
        canopy = 0
        tree_w = 0.0
        canopy_w = 0.0
        for r in rows:
            try:
                dbh = min(int(float(r.get("tree_dbh") or 0)), 36)
            except (ValueError, TypeError):
                dbh = 0
            canopy += dbh
            w = self._kernel(float(r.get("_dist_m") or 0.0), TREE_RADIUS_M)
            tree_w += w
            canopy_w += w * dbh
        return count, canopy, tree_w, canopy_w

    def _count_gardens(self, lat: float, lon: float) -> tuple[int, float]:
        """Count community gardens within GARDEN_RADIUS_M (plain + kernel-weighted)."""
        rows = self._store.query_circle(
            "community_gardens",
            lat=lat,
            lon=lon,
            radius_m=GARDEN_RADIUS_M,
            select="garden_name",
        )
        garden_w = sum(
            self._kernel(float(r.get("_dist_m") or 0.0), GARDEN_RADIUS_M)
            for r in rows
        )
        return len(rows), garden_w

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
