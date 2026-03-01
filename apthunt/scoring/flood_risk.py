"""
FloodRiskScorer — scores listings by FEMA flood zone status.

Uses pre-downloaded PLUTO data (``ds_pluto`` table) to
check the firm07_flag and pfirm15_flag fields for the nearest tax lot.

Score: 100 if NOT in a flood zone, 0 if flagged.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import find_nearest_row


class FloodRiskScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "flood_risk"

    def columns(self) -> dict[str, str]:
        return {
            "flood_firm07": "TEXT",
            "flood_pfirm15": "TEXT",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("pluto", quiet=True)

        results = []

        for lst in listings:
            gh = lst["geohash"]
            lat, lon = lst["lat"], lst["lon"]

            pluto = self._cache.get_or_fetch(
                gh,
                "pluto",
                lambda lat=lat, lon=lon: self._fetch_pluto(lat, lon),
            )

            nearest = find_nearest_row(pluto, lat, lon)
            firm07 = (nearest.get("firm07_flag", "") or "") if nearest else ""
            pfirm15 = (nearest.get("pfirm15_flag", "") or "") if nearest else ""

            in_flood_zone = bool(firm07) or bool(pfirm15)
            score = 0.0 if in_flood_zone else 100.0

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "flood_firm07": firm07 or "",
                        "flood_pfirm15": pfirm15 or "",
                    },
                )
            )

        return results

    def _fetch_pluto(self, lat: float, lon: float) -> list[dict]:
        """Fetch PLUTO lots in a ~300m bounding box from local data."""
        return self._store.query_bbox("pluto", lat, lon, delta=0.0015)


