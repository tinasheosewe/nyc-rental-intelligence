"""
AmenityScorer — scores listings by nearby everyday amenities.

Uses the OpenStreetMap Overpass API (free, no key required) to count
grocery stores, pharmacies, gyms, laundromats, cafés, and restaurants
within a walkable radius.

Scoring:
    Weighted amenity count within 500 m.
    Essentials (grocery, pharmacy) weighted higher than lifestyle
    (café, restaurant).  Percentile-ranked across all listings.

Output columns:
    amenity_grocery   INTEGER — supermarkets + convenience stores
    amenity_pharmacy  INTEGER
    amenity_gym       INTEGER — fitness centres
    amenity_laundry   INTEGER
    amenity_dining    INTEGER — restaurants + cafés
    amenity_total     INTEGER — weighted total
"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request
import urllib.parse
import time

from apthunt.data.block_cache import BlockCache
from apthunt.scoring.base import Scorer, ScorerResult

log = logging.getLogger(__name__)

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Template: {lat}, {lon}, {radius_m}
_OVERPASS_QUERY = """
[out:json][timeout:15];
(
  node["shop"="supermarket"](around:{radius},{lat},{lon});
  node["shop"="convenience"](around:{radius},{lat},{lon});
  node["amenity"="pharmacy"](around:{radius},{lat},{lon});
  node["leisure"="fitness_centre"](around:{radius},{lat},{lon});
  node["shop"="laundry"](around:{radius},{lat},{lon});
  node["amenity"="cafe"](around:{radius},{lat},{lon});
  node["amenity"="restaurant"](around:{radius},{lat},{lon});
);
out tags;
"""

# Category weights (essentials > lifestyle)
_WEIGHTS = {
    "grocery": 3,
    "pharmacy": 3,
    "gym": 2,
    "laundry": 2,
    "dining": 1,
}


class AmenityScorer(Scorer):

    def __init__(self, cache: BlockCache):
        self._cache = cache

    @property
    def name(self) -> str:
        return "amenity"

    def columns(self) -> dict[str, str]:
        return {
            "amenity_grocery": "INTEGER",
            "amenity_pharmacy": "INTEGER",
            "amenity_gym": "INTEGER",
            "amenity_laundry": "INTEGER",
            "amenity_dining": "INTEGER",
            "amenity_total": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        RADIUS_M = 500
        geohash_to_latlon = {
            lst["geohash"]: (lst["lat"], lst["lon"]) for lst in listings
        }

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            cached = self._cache.get(gh, "amenity")
            if cached is not None:
                block_stats[gh] = cached
                continue

            stats = self._query_overpass(lat, lon, RADIUS_M)
            block_stats[gh] = stats
            self._cache.put(gh, "amenity", stats)

        # Percentile-rank by weighted total
        raw = [block_stats[lst["geohash"]]["amenity_total"] for lst in listings]
        pct_scores = _percentile_scores(raw, reverse=False)

        results: list[ScorerResult] = []
        for lst, pct in zip(listings, pct_scores):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=pct,
                    components={
                        "amenity_grocery": stats["amenity_grocery"],
                        "amenity_pharmacy": stats["amenity_pharmacy"],
                        "amenity_gym": stats["amenity_gym"],
                        "amenity_laundry": stats["amenity_laundry"],
                        "amenity_dining": stats["amenity_dining"],
                        "amenity_total": stats["amenity_total"],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------

    def _query_overpass(
        self, lat: float, lon: float, radius_m: int
    ) -> dict:
        """Query Overpass API for amenities near (lat, lon)."""
        query = _OVERPASS_QUERY.format(lat=lat, lon=lon, radius=radius_m)
        data = urllib.parse.urlencode({"data": query}).encode()

        grocery, pharmacy, gym, laundry, dining = 0, 0, 0, 0, 0

        try:
            req = urllib.request.Request(
                _OVERPASS_URL, data=data,
                headers={"User-Agent": "AptHunt/1.0"},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read())

            for el in body.get("elements", []):
                tags = el.get("tags", {})
                shop = tags.get("shop", "")
                amenity = tags.get("amenity", "")
                leisure = tags.get("leisure", "")

                if shop in ("supermarket", "convenience"):
                    grocery += 1
                elif amenity == "pharmacy":
                    pharmacy += 1
                elif leisure == "fitness_centre":
                    gym += 1
                elif shop == "laundry":
                    laundry += 1
                elif amenity in ("cafe", "restaurant"):
                    dining += 1

            # Courtesy rate-limit: Overpass asks for ≤ 1 req/sec
            time.sleep(1.0)

        except Exception as exc:
            log.warning("Overpass query failed for (%.4f, %.4f): %s", lat, lon, exc)

        weighted = (
            grocery * _WEIGHTS["grocery"]
            + pharmacy * _WEIGHTS["pharmacy"]
            + gym * _WEIGHTS["gym"]
            + laundry * _WEIGHTS["laundry"]
            + dining * _WEIGHTS["dining"]
        )

        return {
            "amenity_grocery": grocery,
            "amenity_pharmacy": pharmacy,
            "amenity_gym": gym,
            "amenity_laundry": laundry,
            "amenity_dining": dining,
            "amenity_total": weighted,
        }


def _percentile_scores(
    values: list[float],
    *,
    reverse: bool = False,
) -> list[float]:
    """Convert raw values to 0–100 percentile scores."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]

    indexed = sorted(enumerate(values), key=lambda t: t[1])
    scores = [0.0] * n
    for rank, (idx, _) in enumerate(indexed):
        pct = rank / (n - 1) * 100.0
        scores[idx] = (100.0 - pct) if reverse else pct
    return scores
