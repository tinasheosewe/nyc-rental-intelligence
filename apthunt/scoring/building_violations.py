"""
BuildingViolationsScorer — scores listings by active DOB building violations.

Uses pre-downloaded PLUTO (``ds_pluto``) to map lat/lon → BBL,
then queries pre-downloaded DOB Violations (``ds_dob_violations``)
for that BBL.  Normalises count by residential units so large
buildings aren't unfairly penalised.

Scoring: percentile-rank across all listings in the current universe.
Fewer violations per unit → higher score.
Listing at the median gets 50, best gets ~100, worst gets ~0.
"""

from __future__ import annotations

import sqlite3

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult


class BuildingViolationsScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "building_violations"

    def columns(self) -> dict[str, str]:
        return {
            "building_violation_count": "INTEGER",
            "building_unitsres": "INTEGER",
            "building_violations_per_unit": "REAL",
        }

    # ------------------------------------------------------------------
    # Main scoring loop
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("pluto", quiet=True)
        self._store.ensure_downloaded("dob_violations", quiet=True)

        # Deduplicate by geohash so each block is fetched once
        gh_map: dict[str, tuple[float, float]] = {}
        for lst in listings:
            gh_map.setdefault(lst["geohash"], (lst["lat"], lst["lon"]))

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "building_violations")
            if cached is not None:
                block_stats[gh] = cached
                continue

            # 1. Get PLUTO lots from local data (reuse shared "pluto" cache)
            pluto_rows = self._cache.get_or_fetch(
                gh,
                "pluto",
                lambda lat=lat, lon=lon: self._store.query_bbox(
                    "pluto", lat, lon, delta=0.0015,
                ),
            )

            # 2. Find the nearest lot to the listing point
            nearest = self._find_nearest_lot(pluto_rows, lat, lon)
            if nearest is None:
                stats = {
                    "building_violation_count": 0,
                    "building_unitsres": 1,
                }
                block_stats[gh] = stats
                self._cache.put(gh, "building_violations", stats)
                continue

            bbl_raw = nearest.get("bbl", "")
            unitsres = max(1, int(float(nearest.get("unitsres") or 0)))

            # 3. Parse BBL → boro / block / lot for DOB query
            #    PLUTO BBL is 10 digits: boro(1) + block(5) + lot(4)
            #    DOB stores lot as 5 digits, so zero-pad.
            try:
                bbl_str = str(int(float(bbl_raw))).zfill(10)
                boro = bbl_str[0]
                block = bbl_str[1:6]
                lot = bbl_str[6:10].zfill(5)
            except (ValueError, IndexError):
                stats = {
                    "building_violation_count": 0,
                    "building_unitsres": unitsres,
                }
                block_stats[gh] = stats
                self._cache.put(gh, "building_violations", stats)
                continue

            # 4. Query DOB violations from local data (active only)
            violations = self._fetch_violations(boro, block, lot)
            count = len(violations)

            stats = {
                "building_violation_count": count,
                "building_unitsres": unitsres,
            }
            block_stats[gh] = stats
            self._cache.put(gh, "building_violations", stats)

        # Collect per-unit values for percentile ranking
        per_units: list[tuple[dict, float]] = []
        for lst in listings:
            stats = block_stats[lst["geohash"]]
            count = stats["building_violation_count"]
            unitsres = stats.get("building_unitsres", 1)
            per_unit = count / max(1, unitsres)
            per_units.append((lst, per_unit))

        # Percentile-rank: fewer violations → higher score
        scores = _percentile_scores(
            [pu for _, pu in per_units], reverse=True,
        )

        results: list[ScorerResult] = []
        for i, (lst, per_unit) in enumerate(per_units):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=scores[i],
                    components={
                        "building_violation_count": stats["building_violation_count"],
                        "building_unitsres": stats.get("building_unitsres", 1),
                        "building_violations_per_unit": round(per_unit, 4),
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _fetch_violations(
        self,
        boro: str,
        block: str,
        lot: str,
    ) -> list[dict]:
        """Fetch unresolved DOB violations for a given BBL from local data."""
        return self._store.query(
            "dob_violations",
            where_clause="boro=? AND block=? AND lot=?",
            params=(boro, block, lot),
            select="isn_dob_bis_viol,violation_type,violation_category,issue_date",
        )

    @staticmethod
    def _find_nearest_lot(
        pluto_rows: list[dict],
        lat: float,
        lon: float,
    ) -> dict | None:
        """Return the PLUTO row closest to (lat, lon)."""
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
                 (appropriate for violations where fewer = better).
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]

    indexed = sorted(enumerate(values), key=lambda t: t[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1

    scores = [0.0] * n
    for idx in range(n):
        pct = ranks[idx] / (n - 1) * 100.0
        if reverse:
            pct = 100.0 - pct
        scores[idx] = round(max(0.0, min(100.0, pct)), 1)
    return scores
