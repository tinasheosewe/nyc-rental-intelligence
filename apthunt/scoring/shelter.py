"""
ShelterScorer — scores listings by proximity to homeless shelters/services
AND NYCHA public housing projects.

Uses pre-downloaded datasets:
- NYC Facilities Database filtered for ``NON-RESIDENTIAL HOUSING AND
  HOMELESS SERVICES`` (shelters, drop-in centers, supportive housing).
- NYCHA BBL Extract — all 2,964 public housing buildings across 219
  developments ("the projects").

Scoring model — distance-weighted facility count, median-normalized:

1.  Query all shelters within 800 m and all NYCHA buildings within 800 m.
2.  Weight each by proximity:  weight = 1 − (dist / 800).
    A facility at 0 m gets weight 1.0; at 800 m gets ~0.0.
3.  Sum the weights → ``proximity_weighted_total``.
4.  Normalize against the batch median:
        score = 100 − 50 × (total / median)       when total ≤ median
        score = max(0, 50 − 50 × (excess/median)) when total > median
    *Inverted*: fewer/farther facilities → higher score.

Component columns stored: ``shelter_count``, ``shelter_nearest_m``,
``shelter_nearest_name``, ``shelter_weighted_total``,
``project_count``, ``project_nearest_m``, ``project_nearest_name``.
"""

from __future__ import annotations

import sqlite3

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import dedupe_by_geohash, median_inverse_scores

_RADIUS_M = 800


class ShelterScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "shelter"

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

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "shelter")
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
                dist = haversine((lat, lon), (rlat, rlon), unit=Unit.METERS)
                if dist > _RADIUS_M:
                    continue
                s_count += 1
                proximity = max(0.0, 1.0 - dist / _RADIUS_M)
                s_weighted += proximity
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
                dist = haversine((lat, lon), (rlat, rlon), unit=Unit.METERS)
                if dist > _RADIUS_M:
                    continue
                dev = r.get("development") or "unknown"
                if dev not in dev_nearest or dist < dev_nearest[dev]:
                    dev_nearest[dev] = dist

            p_count = len(dev_nearest)
            p_weighted = sum(
                max(0.0, 1.0 - d / _RADIUS_M) for d in dev_nearest.values()
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
            self._cache.put(gh, "shelter", stats)

        # ── Normalize against batch median (inverted: fewer = better) ──
        baseline = [v["shelter_weighted_total"] for v in block_stats.values()]
        per_listing = [block_stats[lst["geohash"]]["shelter_weighted_total"]
                       for lst in listings]
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
