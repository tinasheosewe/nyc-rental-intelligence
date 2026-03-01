"""
PestScorer — scores listings by pest / vermin exposure.

Combines two independent data sources:

1. **Building-level** — HPD complaints categorised as
   ``major_category='UNSANITARY CONDITION'`` and
   ``minor_category='PESTS'``, looked up by the building's BBL
   (via PLUTO, same approach as ManagementScorer).

2. **Area-level** — 311 Service Requests with ``complaint_type='Rodent'``
   within 300 m (same radius as NoiseScorer; data already in ``ds_noise``).

Scoring:
    Percentile-rank across all listings by ``pest_total``
    (HPD pests + 311 rodents).  Lower totals → higher score.

Output columns:
    pest_hpd_count     INTEGER  — HPD pest complaints for the building (12 mo)
    pest_rodent_count  INTEGER  — 311 rodent complaints within 300 m
    pest_total         INTEGER  — combined total
"""

from __future__ import annotations

import sqlite3

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult


RADIUS_M = 300


class PestScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "pest"

    def columns(self) -> dict[str, str]:
        return {
            "pest_hpd_count": "INTEGER",
            "pest_rodent_count": "INTEGER",
            "pest_total": "INTEGER",
        }

    # ------------------------------------------------------------------
    # Main scoring entry point
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("pluto", quiet=True)
        self._store.ensure_downloaded("hpd_complaints", quiet=True)
        self._store.ensure_downloaded("noise", quiet=True)

        # Deduplicate by geohash (one lookup per block)
        gh_map: dict[str, tuple[float, float]] = {}
        for lst in listings:
            gh_map.setdefault(lst["geohash"], (lst["lat"], lst["lon"]))

        block_stats: dict[str, dict] = {}

        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "pest")
            if cached is not None:
                block_stats[gh] = cached
                continue

            # --- Building-level: HPD pest complaints via BBL ---------------
            hpd_count = self._hpd_pest_count(lat, lon)

            # --- Area-level: 311 rodent complaints within 300 m ------------
            rodent_count = self._rodent_count(lat, lon)

            stats = {
                "pest_hpd_count": hpd_count,
                "pest_rodent_count": rodent_count,
                "pest_total": hpd_count + rodent_count,
            }
            block_stats[gh] = stats
            self._cache.put(gh, "pest", stats)

        # Percentile-rank by pest_total (lower = better)
        raw = [block_stats[lst["geohash"]]["pest_total"] for lst in listings]
        pct_scores = _percentile_scores(raw, reverse=True)

        results: list[ScorerResult] = []
        for lst, pct in zip(listings, pct_scores):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=pct,
                    components={
                        "pest_hpd_count": stats["pest_hpd_count"],
                        "pest_rodent_count": stats["pest_rodent_count"],
                        "pest_total": stats["pest_total"],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _hpd_pest_count(self, lat: float, lon: float) -> int:
        """Count HPD pest complaints for the nearest PLUTO lot's BBL."""
        # Reuse PLUTO data already cached by management scorer
        pluto_rows = self._store.query_bbox(
            "pluto", lat, lon, delta=0.0015,
        )
        nearest = self._find_nearest_lot(pluto_rows, lat, lon)

        if nearest is None:
            return 0

        raw_bbl = nearest.get("bbl")
        if not raw_bbl:
            return 0

        try:
            bbl = str(int(float(raw_bbl)))
        except (ValueError, TypeError):
            bbl = str(raw_bbl)

        rows = self._store.query(
            "hpd_complaints",
            where_clause=(
                "bbl = ? "
                "AND major_category = 'UNSANITARY CONDITION' "
                "AND minor_category = 'PESTS'"
            ),
            params=(bbl,),
            select="COUNT(*) as cnt",
        )
        return int(rows[0]["cnt"]) if rows else 0

    def _rodent_count(self, lat: float, lon: float) -> int:
        """Count 311 rodent complaints within RADIUS_M metres."""
        rows = self._store.query_circle(
            "noise",
            lat=lat,
            lon=lon,
            radius_m=RADIUS_M,
            select="complaint_type",
        )
        return sum(1 for r in rows if r.get("complaint_type") == "Rodent")

    @staticmethod
    def _find_nearest_lot(
        pluto_rows: list[dict],
        lat: float,
        lon: float,
    ) -> dict | None:
        if not pluto_rows:
            return None
        best, best_d = None, float("inf")
        for row in pluto_rows:
            try:
                rlat = float(row["latitude"])
                rlon = float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            d = haversine((lat, lon), (rlat, rlon), unit=Unit.METERS)
            if d < best_d:
                best, best_d = row, d
        return best


def _percentile_scores(
    values: list[float],
    *,
    reverse: bool = False,
) -> list[float]:
    """Convert raw values to 0–100 percentile scores.

    Args:
        values:  One value per listing.
        reverse: If True, *lower* raw values get *higher* scores
                 (good for complaint counts).
    """
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
