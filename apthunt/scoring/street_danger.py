"""
StreetDangerScorer — scores listings by pedestrian / cyclist street danger.

Data source: ``ds_street_collisions`` (NYPD Motor Vehicle Collisions,
pre-filtered at download time to crashes that injured or killed a
pedestrian or cyclist within the last 12 months).

Method:
    All qualifying crashes within 300 m of the listing's block are
    combined into a single weighted exposure metric.  Each crash
    contributes::

        severity = (ped_injured + cyc_injured) + 10 * (ped_killed + cyc_killed)
        weight   = severity * exp(-(d / 150)**2) * decay_weight(crash_date)

    i.e. a Gaussian distance kernel (sigma 150 m — a crash at the corner
    matters far more than one 300 m away) times the standard 6-month
    half-life recency decay.  Deaths count 10x injuries.

    The weighted sum alone is population-density confounded: crowded
    neighborhoods generate more crash *volume* at identical per-person
    risk.  So the scored metric is a per-household rate::

        street_danger_rate_per_khh =
            1000 * street_danger_weighted
                 / kernel_weighted_units(store, lat, lon, 300)

    i.e. weighted casualties per 1000 households, using the same Gaussian
    kernel and radius on the denominator (PLUTO residential units).
    Caveat: street injuries track foot traffic, and foot traffic tracks
    more than residents (transit, retail, nightlife), so per-household is
    an imperfect exposure proxy here — but it is far better than raw
    counts, which just measure crowdedness.

    The rate is scored against the frozen citywide baseline (lower =
    better; zero is perfect).  Until a baseline exists, an absolute
    exponential fallback is used: ``100 * exp(-rate / 8)`` — no nearby
    crashes scores 100 and the score approaches 0 for chronic
    high-injury corridors.

Output columns:
    street_danger_injuries      INTEGER — ped + cyclist injuries within 300 m (12 mo)
    street_danger_deaths        INTEGER — ped + cyclist deaths within 300 m (12 mo)
    street_danger_weighted      REAL    — severity x distance-kernel x recency sum
    street_danger_rate_per_khh  REAL    — weighted sum per 1000 kernel-weighted households
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores, decay_weight
from apthunt.scoring.utils import dedupe_by_geohash, kernel_weighted_units


RADIUS_M = 300
KERNEL_SIGMA_M = 150.0
DEATH_WEIGHT = 10.0
# Absolute fallback scale: score = 100 * exp(-rate / FALLBACK_SCALE).
# rate ~= recency/distance-discounted casualties per 1000 households,
# so 0 -> 100, ~5.5 -> 50, ~18 -> 10.
FALLBACK_SCALE = 8.0

_CACHE_KEY = "street_danger_v2"


class StreetDangerScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "street_danger"

    # Citywide baseline metric (sampled by scripts/build_baseline.py).
    # Per-household rate, not the raw weighted count — raw counts are
    # population-density confounded (see module docstring).
    baseline_component = "street_danger_rate_per_khh"
    baseline_reverse = True
    baseline_zero_perfect = True

    def columns(self) -> dict[str, str]:
        return {
            "street_danger_injuries": "INTEGER",
            "street_danger_deaths": "INTEGER",
            "street_danger_weighted": "REAL",
            "street_danger_rate_per_khh": "REAL",
        }

    # ------------------------------------------------------------------
    # Main scoring entry point
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("street_collisions", quiet=True)

        # The dataset may still be mid-download in another process — probe
        # once and degrade gracefully if the table isn't there yet.
        try:
            self._store.query("street_collisions", select="collision_id", limit=1)
        except sqlite3.OperationalError:
            return [
                ScorerResult(listing_id=lst["id"], score=None, components={})
                for lst in listings
            ]

        # Deduplicate by geohash (one lookup per block)
        gh_map = dedupe_by_geohash(listings)

        today_ord = datetime.now().toordinal()

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, _CACHE_KEY)
            if cached is not None:
                block_stats[gh] = cached
                continue
            stats = self._block_danger(lat, lon, today_ord)
            block_stats[gh] = stats
            self._cache.put(gh, _CACHE_KEY, stats)

        # Score the per-household rate against the frozen citywide
        # baseline (lower = better; zero_is_perfect: no nearby ped/cyc
        # casualties -> 100).
        raw = [
            block_stats[lst["geohash"]]["street_danger_rate_per_khh"]
            for lst in listings
        ]
        scores = baseline_scores(
            conn, self.name, raw, reverse=True, zero_is_perfect=True,
        )
        if scores is None:
            # Absolute fallback until the first baseline build: exponential
            # decay of the weighted casualty burden (defined for a single
            # listing, no batch-relative artifacts).
            scores = [
                round(100.0 * math.exp(-float(v) / FALLBACK_SCALE), 1)
                for v in raw
            ]

        results: list[ScorerResult] = []
        for lst, sc in zip(listings, scores):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=sc,
                    components={
                        "street_danger_injuries": stats["street_danger_injuries"],
                        "street_danger_deaths": stats["street_danger_deaths"],
                        "street_danger_weighted": stats["street_danger_weighted"],
                        "street_danger_rate_per_khh": stats[
                            "street_danger_rate_per_khh"
                        ],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _block_danger(self, lat: float, lon: float, today_ord: int) -> dict:
        """Weighted ped/cyclist casualty burden within RADIUS_M metres."""
        rows = self._store.query_circle(
            "street_collisions",
            lat=lat,
            lon=lon,
            radius_m=RADIUS_M,
            select=(
                "crash_date,"
                "number_of_pedestrians_injured,number_of_pedestrians_killed,"
                "number_of_cyclist_injured,number_of_cyclist_killed"
            ),
        )

        injuries = 0
        deaths = 0
        weighted = 0.0
        for r in rows:
            inj = _num(r.get("number_of_pedestrians_injured")) + _num(
                r.get("number_of_cyclist_injured")
            )
            kil = _num(r.get("number_of_pedestrians_killed")) + _num(
                r.get("number_of_cyclist_killed")
            )
            if inj <= 0 and kil <= 0:
                continue
            injuries += int(inj)
            deaths += int(kil)

            severity = inj + DEATH_WEIGHT * kil
            dist = float(r.get("_dist_m") or 0.0)
            kernel = math.exp(-((dist / KERNEL_SIGMA_M) ** 2))
            recency = decay_weight((r.get("crash_date") or "")[:10], today_ord)
            weighted += severity * kernel * recency

        # Density-corrected rate: weighted casualties per 1000 kernel-
        # weighted households (same radius on both sides).  Imperfect —
        # street injuries correlate with foot traffic, which includes
        # non-residents (transit, retail, nightlife) — but far better
        # than raw counts, which mostly measure crowdedness.
        units = kernel_weighted_units(self._store, lat, lon, RADIUS_M)
        rate = 1000.0 * weighted / units

        return {
            "street_danger_injuries": injuries,
            "street_danger_deaths": deaths,
            "street_danger_weighted": round(weighted, 4),
            "street_danger_rate_per_khh": round(rate, 4),
        }


def _num(v) -> float:
    """Coerce a possibly-NULL/text numeric field to float (0.0 on junk)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
