"""
ConvenienceScorer — scores listings by nearby everyday conveniences.

Uses pre-downloaded OpenStreetMap amenity data (via DataStore) to count
grocery stores, pharmacies, gyms, laundromats, cafés, and restaurants
within a walkable radius, plus the NYS retail-food-store license file
(ds_supermarkets) for grocery *quality* tiers.

Scoring (citywide baseline, 0–100):
    Each amenity is weighted by a Gaussian distance kernel
    exp(-(d / (radius/2))²) so a grocery at the corner counts more than
    one at the edge of the radius.  The kernel-weighted per-category sums
    feed the sqrt diminishing-returns formula:

        grocery_pts     = sqrt(min(1, grocery / 8))     × 15
        supermarket_pts = sqrt(min(1, supermkt / 3))    × 15
        pharmacy_pts    = sqrt(min(1, pharmacy / 4))    × 20
        dining_pts      = sqrt(min(1, dining / 30))     × 20
        gym_pts         = sqrt(min(1, gym / 4))         × 15
        laundry_pts     = sqrt(min(1, laundry / 3))     × 15

        convenience_weighted = grocery_pts + ... + laundry_pts

    Grocery quality tiers (ds_supermarkets, licensed square footage):
        >= 6,000 sqft  → full supermarket, tier weight ×1.5
        3,000–6,000    → mid supermarket, tier weight ×1.0
        <  3,000/NULL  → bodega-class: folded into the OSM grocery kernel
                         at low weight (×0.25) rather than the supermarket
                         term — a licensed corner store is corroborating
                         bodega signal, not full-market access.
    When ds_supermarkets is unavailable (re-download in flight) the
    grocery term keeps its legacy ×30 weight so totals stay comparable.

    Pedestrian severance: the *nearest* supermarket and *nearest* OSM
    grocery have their kernel distance inflated by
    path_severance_penalty_m (effective extra meters for crossing
    hostile roads).  Applied to those two dominant contributors ONLY —
    they carry most of the kernel mass for the everyday shopping trip,
    and per-point severance across every café/gym/pharmacy would cost
    ~25 road queries per amenity per block for signal that the kernel
    tail already renders negligible.

    That raw metric is then mapped to its citywide percentile via
    baseline_scores (more amenities = better).  Until the first baseline
    build it falls back to the raw formula applied to unweighted counts.

Output columns:
    convenience_grocery              INTEGER — supermarkets + convenience stores (OSM)
    convenience_pharmacy             INTEGER
    convenience_gym                  INTEGER — fitness centres
    convenience_laundry              INTEGER
    convenience_dining               INTEGER — restaurants + cafés
    convenience_total                INTEGER — weighted total (legacy, kept for reference)
    convenience_weighted             REAL    — kernel-weighted sqrt-cap metric (drives score)
    convenience_supermarket_count    INTEGER — licensed stores >= 3,000 sqft in radius
                                               (NULL while ds_supermarkets unavailable)
    convenience_supermarket_nearest_m REAL   — severance-adjusted effective walking
                                               meters to the nearest such supermarket
                                               (NULL if none in radius / no data)
"""

from __future__ import annotations

import logging
import math
import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores
from apthunt.scoring.utils import dedupe_by_geohash, path_severance_penalty_m

log = logging.getLogger(__name__)

# Cache version: v4 — supermarket quality tiers + severance on the
# nearest-supermarket / nearest-grocery paths (metric semantics changed).
# Blocks computed while ds_supermarkets is unavailable use a ":nosm"
# suffix so they are recomputed once the re-download lands.
_CACHE_KEY = "convenience_v4"

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

# ── Supermarket quality tiers (ds_supermarkets square footage) ───────
_SM_FULL_SQFT = 6000.0      # full supermarket
_SM_MID_SQFT = 3000.0       # mid-size market
_SM_FULL_TIER_WEIGHT = 1.5
_SM_MID_TIER_WEIGHT = 1.0
# Bodega-class (< 3k sqft or unknown sqft) rows fold into the OSM grocery
# kernel at low weight — most already appear there as OSM 'convenience',
# so a low weight limits double counting while still crediting licensed
# corner stores OSM missed.
_SM_BODEGA_GROCERY_WEIGHT = 0.25
_SM_CAP = 3.0               # tier-weighted kernel supermarkets to saturate
_SM_SCORE_WEIGHT = 15       # points; taken from grocery's legacy 30
_GROCERY_WEIGHT_WITH_SM = 15


class ConvenienceScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache
        self._store.ensure_downloaded("amenities", quiet=True)
        # Best-effort: supermarket licenses may still be re-downloading;
        # score() probes availability and degrades gracefully.
        try:
            self._store.ensure_downloaded("supermarkets", quiet=True)
        except Exception:
            log.info("convenience: ds_supermarkets not available yet")

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
            "convenience_supermarket_count": "INTEGER",
            "convenience_supermarket_nearest_m": "REAL",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        RADIUS_M = 500
        geohash_to_latlon = dedupe_by_geohash(listings)

        # Probe once per score() call: re-downloads are in flight, so the
        # supermarkets table (or its extracted geo/sqft columns) may be
        # missing right now.
        sm_available = self._probe_supermarkets()
        cache_key = _CACHE_KEY if sm_available else _CACHE_KEY + ":nosm"

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            cached = self._cache.get(gh, cache_key)
            if cached is not None:
                block_stats[gh] = cached
                continue

            stats = self._count_nearby(lat, lon, RADIUS_M, sm_available)
            block_stats[gh] = stats
            self._cache.put(gh, cache_key, stats)

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
                        "convenience_supermarket_count": stats.get(
                            "convenience_supermarket_count"
                        ),
                        "convenience_supermarket_nearest_m": stats.get(
                            "convenience_supermarket_nearest_m"
                        ),
                    },
                )
            )
        return results

    def _probe_supermarkets(self) -> bool:
        """True when ds_supermarkets is queryable with sqft + extracted geo."""
        try:
            self._store.query(
                "supermarkets",
                select="square_footage, latitude, longitude",
                limit=1,
            )
            return True
        except Exception:
            return False

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
    def _weighted_metric(kernel_counts: dict, sm_kernel: float | None) -> float:
        """Sqrt-cap formula applied to kernel-weighted category sums.

        ``sm_kernel`` is the tier-weighted supermarket kernel sum, or None
        when ds_supermarkets is unavailable — in which case grocery keeps
        its legacy 30-point weight so the total scale is preserved.
        """
        total = 0.0
        for cat in _CATEGORIES:
            weight = _SCORE_WEIGHTS[cat]
            if cat == "grocery" and sm_kernel is not None:
                weight = _GROCERY_WEIGHT_WITH_SM
            total += math.sqrt(min(1.0, kernel_counts[cat] / _CAPS[cat])) * weight
        if sm_kernel is not None:
            total += math.sqrt(min(1.0, sm_kernel / _SM_CAP)) * _SM_SCORE_WEIGHT
        return round(total, 2)

    # ------------------------------------------------------------------

    def _count_nearby(
        self, lat: float, lon: float, radius_m: int, sm_available: bool
    ) -> dict:
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
        nearest_grocery = None  # (dist_m, lat, lon) — severance target
        for row in nearby:
            cat = row.get("category", "")
            if cat not in counts:
                continue
            counts[cat] += 1
            d = row.get("_dist_m", 0.0) or 0.0
            kernel_counts[cat] += math.exp(-((d / sigma) ** 2))
            if cat == "grocery" and (
                nearest_grocery is None or d < nearest_grocery[0]
            ):
                try:
                    nearest_grocery = (d, float(row["lat"]), float(row["lon"]))
                except (KeyError, TypeError, ValueError):
                    pass

        # Pedestrian severance on the dominant contributor only: the
        # nearest grocery carries most of the kernel mass for the everyday
        # shopping trip. Per-point severance on all amenities would be
        # ~25 road queries × every amenity per block — too heavy for
        # contributions the kernel tail already makes negligible.
        if nearest_grocery is not None:
            d, glat, glon = nearest_grocery
            penalty = path_severance_penalty_m(self._store, lat, lon, glat, glon)
            if penalty > 0.0:
                kernel_counts["grocery"] += (
                    math.exp(-(((d + penalty) / sigma) ** 2))
                    - math.exp(-((d / sigma) ** 2))
                )

        # Supermarket quality tiers from licensed square footage.
        sm_kernel: float | None = None
        sm_count = None
        sm_nearest_m = None
        if sm_available:
            sm_kernel, sm_count, sm_nearest_m = self._supermarket_kernel(
                lat, lon, radius_m, sigma, kernel_counts
            )

        weighted = sum(counts[cat] * _WEIGHTS[cat] for cat in _CATEGORIES)

        return {
            "convenience_grocery": counts["grocery"],
            "convenience_pharmacy": counts["pharmacy"],
            "convenience_gym": counts["gym"],
            "convenience_laundry": counts["laundry"],
            "convenience_dining": counts["dining"],
            "convenience_total": weighted,
            "convenience_weighted": self._weighted_metric(kernel_counts, sm_kernel),
            "convenience_supermarket_count": sm_count,
            "convenience_supermarket_nearest_m": sm_nearest_m,
        }

    def _supermarket_kernel(
        self,
        lat: float,
        lon: float,
        radius_m: int,
        sigma: float,
        kernel_counts: dict,
    ):
        """Tier-weighted supermarket kernel sum from ds_supermarkets.

        Returns ``(sm_kernel, sm_count, sm_nearest_m)`` where sm_nearest_m
        is the severance-adjusted effective walking distance to the nearest
        >= 3,000 sqft store. Bodega-class rows (< 3k sqft or unknown sqft)
        are folded into ``kernel_counts["grocery"]`` at low weight in place
        (mutates the dict). Any query failure degrades to (None, None, None)
        — the caller then scores with the legacy grocery-only weighting.
        """
        try:
            rows = self._store.query_circle(
                "supermarkets", lat, lon, radius_m,
                select="square_footage",
            )
        except Exception:
            return None, None, None

        sm_kernel = 0.0
        sm_count = 0
        nearest = None  # (dist_m, lat, lon) of nearest >=3k sqft store
        for row in rows:
            d = row.get("_dist_m", 0.0) or 0.0
            try:
                sqft = float(row["square_footage"])
            except (KeyError, TypeError, ValueError):
                sqft = 0.0  # unknown size → treat as bodega-class
            if sqft >= _SM_FULL_SQFT:
                tier_w = _SM_FULL_TIER_WEIGHT
            elif sqft >= _SM_MID_SQFT:
                tier_w = _SM_MID_TIER_WEIGHT
            else:
                # Bodega-class: corroborating grocery signal, low weight.
                kernel_counts["grocery"] += (
                    _SM_BODEGA_GROCERY_WEIGHT * math.exp(-((d / sigma) ** 2))
                )
                continue
            sm_count += 1
            sm_kernel += tier_w * math.exp(-((d / sigma) ** 2))
            if nearest is None or d < nearest[0]:
                try:
                    nearest = (
                        d,
                        float(row["latitude"]),
                        float(row["longitude"]),
                        tier_w,
                    )
                except (KeyError, TypeError, ValueError):
                    pass

        sm_nearest_m = None
        if nearest is not None:
            d, slat, slon, tier_w = nearest
            # Severance on the dominant contributor only (see module doc).
            penalty = path_severance_penalty_m(self._store, lat, lon, slat, slon)
            sm_nearest_m = round(d + penalty, 1)
            if penalty > 0.0:
                sm_kernel += tier_w * (
                    math.exp(-(((d + penalty) / sigma) ** 2))
                    - math.exp(-((d / sigma) ** 2))
                )
        return round(sm_kernel, 3), sm_count, sm_nearest_m
