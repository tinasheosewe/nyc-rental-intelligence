"""
TransitScorer — scores listings by subway station proximity and route diversity.

Uses MTA GTFS stops.txt data (loaded into TransitData) and caches
results in the block_cache keyed by geohash + "transit".

Scoring formula:
    score = min(100, station_count * 12 + route_count * 3)

    5+ stations with 8+ routes in 800m → 100 (excellent transit)
    0 stations → 0 (transit desert)
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.transit_data import TransitData, _haversine
from apthunt.scoring.base import Scorer, ScorerResult



class TransitScorer(Scorer):
    def __init__(self, transit_data: TransitData, cache: BlockCache):
        self._transit = transit_data
        self._cache = cache

    @property
    def name(self) -> str:
        return "transit"

    def columns(self) -> dict[str, str]:
        return {
            "transit_station_count": "INTEGER",
            "transit_routes_served": "INTEGER",
            "transit_nearest_m": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        results = []
        for lst in listings:
            gh = lst["geohash"]
            cached = self._cache.get(gh, "transit")
            if cached is not None:
                station_count = cached["station_count"]
                routes = cached["routes_served"]
                nearest = cached["nearest_m"]
            else:
                lat, lon = lst["lat"], lst["lon"]
                stations = self._transit.stations_within(lat, lon, 800)
                station_count = len(stations)
                routes = len(set(r for s in stations for r in s.routes))
                if stations:
                    nearest = int(min(_haversine(lat, lon, s.lat, s.lon) for s in stations))
                else:
                    nearest = 9999
                self._cache.put(
                    gh,
                    "transit",
                    {
                        "station_count": station_count,
                        "routes_served": routes,
                        "nearest_m": nearest,
                    },
                )
            raw = station_count * 12 + routes * 3
            score = round(max(0.0, min(100.0, float(raw))), 1)
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "transit_station_count": station_count,
                        "transit_routes_served": routes,
                        "transit_nearest_m": nearest,
                    },
                )
            )
        return results
