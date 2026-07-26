"""
ShelterScorer — scores listings by proximity to homeless shelters/services
AND NYCHA public housing projects.

Uses pre-downloaded datasets:
- NYC Facilities Database filtered for ``NON-RESIDENTIAL HOUSING AND
  HOMELESS SERVICES`` (shelters, drop-in centers, supportive housing).
- NYCHA BBL Extract — all 2,964 public housing buildings across 219
  developments ("the projects").

Scoring model — distance-weighted facility count, baseline-normalized:

1.  Query all shelters within 800 m and all NYCHA buildings within 800 m.
2.  Weight each by a Gaussian proximity kernel:
        weight = exp(−(dist / (800/2))²)
    A facility at 0 m gets weight 1.0; at 800 m gets ~0.02.
3.  Sum the weights → ``proximity_weighted_total``.
4.  Score against the frozen citywide baseline distribution
    (``baseline_scores``); *inverted*: fewer/farther facilities →
    higher score, and a total of exactly 0 pins to 100.
    Falls back to batch-median normalization until the first
    ``build_baseline.py`` run.

Component columns stored: ``shelter_count``, ``shelter_nearest_m``,
``shelter_nearest_name``, ``shelter_weighted_total``,
``project_count``, ``project_nearest_m``, ``project_nearest_name``.
"""

from __future__ import annotations

import sqlite3
from math import exp

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores
from apthunt.scoring.utils import dedupe_by_geohash, median_inverse_scores

_RADIUS_M = 800


class ShelterScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "shelter"

    # Citywide baseline: sampled by scripts/build_baseline.py.
    baseline_component = "shelter_weighted_total"
    baseline_reverse = True          # fewer/closer facilities weighted less = better
    baseline_zero_perfect = True     # no facilities within 800 m → 100

    def columns(self) -> dict[str, str]:
        return {
            "shelter_count": "INTEGER",
            "shelter_nearest_m": "INTEGER",
            "shelter_nearest_name": "TEXT",
            "shelter_weighted_total": "REAL",
            "project_count": "INTEGER",
            "project_nearest_m": "INTEGER",
            "project_nearest_name": "TEXT",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("shelters", quiet=True)
        self._store.ensure_downloaded("projects", quiet=True)

        # Deduplicate by geohash
        gh_map = dedupe_by_geohash(listings)

        # Gaussian distance kernel: 1.0 at the door, ~0.02 at the radius edge.
        _sigma = _RADIUS_M / 2.0

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "shelter_v2")
            if cached is not None:
                block_stats[gh] = cached
                continue

            # ── Shelters ─────────────────────────────────────
            shelter_rows = self._store.query_circle(
                "shelters",
                lat=lat, lon=lon, radius_m=_RADIUS_M,
                select="facname,latitude,longitude",
            )

            s_count = 0
            s_weighted = 0.0
            s_nearest_m = 9999
            s_nearest_name = ""

            for r in shelter_rows:
                rlat = float(r.get("latitude") or 0)
                rlon = float(r.get("longitude") or 0)
                if rlat == 0 or rlon == 0:
                    continue
                dist = float(r["_dist_m"])
                if dist > _RADIUS_M:
                    continue
                s_count += 1
                s_weighted += exp(-((dist / _sigma) ** 2))
                if dist < s_nearest_m:
                    s_nearest_m = int(dist)
                    s_nearest_name = r.get("facname") or ""

            # ── NYCHA projects ───────────────────────────────
            # Group by development name so that one housing complex
            # with many buildings counts as ONE facility, not N.
            project_rows = self._store.query_circle(
                "projects",
                lat=lat, lon=lon, radius_m=_RADIUS_M,
                select="development,latitude,longitude",
            )

            # dev_name → nearest distance (m)
            dev_nearest: dict[str, float] = {}

            for r in project_rows:
                rlat = float(r.get("latitude") or 0)
                rlon = float(r.get("longitude") or 0)
                if rlat == 0 or rlon == 0:
                    continue
                dist = float(r["_dist_m"])
                if dist > _RADIUS_M:
                    continue
                dev = r.get("development") or "unknown"
                if dev not in dev_nearest or dist < dev_nearest[dev]:
                    dev_nearest[dev] = dist

            p_count = len(dev_nearest)
            p_weighted = sum(
                exp(-((d / _sigma) ** 2)) for d in dev_nearest.values()
            )
            p_nearest_m = 9999
            p_nearest_name = ""
            if dev_nearest:
                nearest_dev = min(dev_nearest, key=dev_nearest.get)
                p_nearest_m = int(dev_nearest[nearest_dev])
                p_nearest_name = nearest_dev

            # ── Combined weighted total ──────────────────────
            weighted_total = round(s_weighted + p_weighted, 3)

            stats = {
                "shelter_count": s_count,
                "shelter_nearest_m": s_nearest_m if s_count > 0 else 9999,
                "shelter_nearest_name": s_nearest_name,
                "shelter_weighted_total": weighted_total,
                "project_count": p_count,
                "project_nearest_m": p_nearest_m if p_count > 0 else 9999,
                "project_nearest_name": p_nearest_name,
            }
            block_stats[gh] = stats
            self._cache.put(gh, "shelter_v2", stats)

        # ── Score against the citywide baseline (inverted: fewer = better);
        #    fall back to batch-median until the first baseline build ──
        per_listing = [block_stats[lst["geohash"]]["shelter_weighted_total"]
                       for lst in listings]
        scores = baseline_scores(
            conn, self.name, per_listing,
            reverse=True, zero_is_perfect=True,
        )
        if scores is None:
            baseline = [v["shelter_weighted_total"] for v in block_stats.values()]
            scores = median_inverse_scores(per_listing, baseline=baseline)

        results: list[ScorerResult] = []
        for lst, sc in zip(listings, scores):
            stats = block_stats[lst["geohash"]]

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=round(sc, 1),
                    components={
                        "shelter_count": stats["shelter_count"],
                        "shelter_nearest_m": stats["shelter_nearest_m"],
                        "shelter_nearest_name": stats["shelter_nearest_name"],
                        "shelter_weighted_total": stats["shelter_weighted_total"],
                        "project_count": stats["project_count"],
                        "project_nearest_m": stats["project_nearest_m"],
                        "project_nearest_name": stats["project_nearest_name"],
                    },
                )
            )
        return results
