"""
AirQualityScorer — scores listings by ambient air pollution.

Data source: ``ds_air_quality`` — NYCCAS (NYC Community Air Survey)
annual pollutant surfaces aggregated by **community district**
(``geo_type_name='CD'``, ``geo_join_id`` like '101' = boro digit +
CD number).  Indicators used:

* ``Fine particles (PM 2.5)`` — µg/m³ (annual average)
* ``Nitrogen dioxide (NO2)``  — ppb   (annual average)

Method:
    nearest PLUTO lot → its ``cd`` code → latest NYCCAS rows for that
    community district.  The combined raw index is

        air_quality_index = pm25 + 0.3 * no2

    which puts the two pollutants on a comparable scale (NYC annual
    PM2.5 ≈ 6–9 µg/m³, NO2 ≈ 12–30 ppb, so 0.3·NO2 ≈ 3.6–9).

    Lower is better (``reverse=True``); zero is NOT perfect — zero
    ambient air pollution does not exist, so no ``zero_is_perfect`` pin.

Output columns:
    air_quality_pm25   REAL — latest annual PM2.5 for the CD (µg/m³)
    air_quality_no2    REAL — latest annual NO2 for the CD (ppb)
    air_quality_index  REAL — pm25 + 0.3 * no2 (combined raw index)

Robustness:
    ``ds_pluto.cd`` only exists after a re-download of PLUTO, and
    ``ds_air_quality`` may still be downloading.  A missing table OR a
    missing ``cd`` column is probed up front; either one makes the
    scorer return score=None / components={} for every listing.
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores
from apthunt.scoring.utils import dedupe_by_geohash, find_nearest_row

# NYCCAS indicator names (exact strings in ds_air_quality.name)
_PM25 = "Fine particles (PM 2.5)"
_NO2 = "Nitrogen dioxide (NO2)"

# NO2 weight in the combined index (ppb → comparable to µg/m³ PM2.5)
NO2_WEIGHT = 0.3

# Absolute fallback anchors (used only before the first baseline build):
# index ≈ 8  → WHO-guideline-clean by NYC standards (PM2.5 ~5 + 0.3·NO2 ~10)
# index ≈ 20 → the dirtiest NYC CDs (Midtown-class PM2.5 ~9 + NO2 ~35)
_INDEX_BEST = 8.0
_INDEX_WORST = 20.0


class AirQualityScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "air_quality"

    # Citywide baseline metric (sampled by scripts/build_baseline.py)
    baseline_component = "air_quality_index"
    baseline_reverse = True
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "air_quality_pm25": "REAL",
            "air_quality_no2": "REAL",
            "air_quality_index": "REAL",
        }

    # ------------------------------------------------------------------
    # Main scoring entry point
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        try:
            self._store.ensure_downloaded("pluto", quiet=True)
            self._store.ensure_downloaded("air_quality", quiet=True)
        except Exception:
            pass  # download in flight / failed — probe below decides

        # Probe both prerequisites: ds_air_quality table AND the new
        # ds_pluto 'cd' column (absent until PLUTO re-downloads). Either
        # missing → score None for everything, crash for nothing.
        try:
            self._store.query("air_quality", select="geo_join_id", limit=1)
            self._store.query("pluto", select="cd", limit=1)
        except sqlite3.OperationalError:
            return [
                ScorerResult(listing_id=lst["id"], score=None, components={})
                for lst in listings
            ]

        # Deduplicate by geohash (one lookup per block)
        gh_map = dedupe_by_geohash(listings)

        block_stats: dict[str, dict] = {}
        cd_metrics: dict[int, dict] = {}  # per-CD memo within this batch

        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, "air_quality_v1")
            if cached is not None:
                block_stats[gh] = cached
                continue

            # --- nearest PLUTO lot → community district ---------------
            pluto_rows = self._store.query_bbox(
                "pluto", lat, lon, delta=0.0015,
                select="latitude,longitude,cd",
            )
            nearest = find_nearest_row(pluto_rows, lat, lon)
            cd = _norm_cd(nearest.get("cd")) if nearest else None

            if cd is None:
                stats = {
                    "air_quality_pm25": None,
                    "air_quality_no2": None,
                    "air_quality_index": None,
                }
            else:
                if cd not in cd_metrics:
                    cd_metrics[cd] = self._cd_metrics(cd)
                stats = cd_metrics[cd]

            block_stats[gh] = stats
            self._cache.put(gh, "air_quality_v1", stats)

        # Score the combined index against the frozen citywide baseline
        # (lower = better; no zero-is-perfect pin — zero pollution
        # doesn't exist in ambient air).
        raw = [
            block_stats[lst["geohash"]]["air_quality_index"]
            for lst in listings
        ]
        # DELIBERATELY ABSOLUTE, not percentile: NYC's intra-city air
        # differences are modest in health terms (PM2.5 ~6-11 µg/m³ —
        # all far below EPA action levels). Percentiling that narrow
        # range manufactured drama ("worse than 95% of NYC") for
        # differences no resident can perceive. Anchored to health
        # guidance instead: the worst NYC districts read "below
        # average", not "hazardous".
        scores = [_absolute_score(v) for v in raw]

        results: list[ScorerResult] = []
        for lst, sc in zip(listings, scores):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=sc,
                    components={
                        "air_quality_pm25": stats["air_quality_pm25"],
                        "air_quality_no2": stats["air_quality_no2"],
                        "air_quality_index": stats["air_quality_index"],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cd_metrics(self, cd: int) -> dict:
        """Latest annual PM2.5 / NO2 for one community district."""
        rows = self._store.query(
            "air_quality",
            where_clause=(
                "geo_type_name = 'CD' "
                "AND CAST(geo_join_id AS INTEGER) = ? "
                "AND name IN (?, ?)"
            ),
            params=(cd, _PM25, _NO2),
            select="name,time_period,start_date,data_value",
        )
        pm25 = _latest_value(rows, _PM25)
        no2 = _latest_value(rows, _NO2)

        index = None
        if pm25 is not None and no2 is not None:
            index = round(pm25 + NO2_WEIGHT * no2, 3)
        elif pm25 is not None:
            index = round(pm25, 3)  # degrade gracefully to PM2.5-only

        return {
            "air_quality_pm25": pm25,
            "air_quality_no2": no2,
            "air_quality_index": index,
        }


def _norm_cd(raw) -> "int | None":
    """Normalize a PLUTO cd value ('101', '101.0', 101.0) to int 101."""
    if raw in (None, ""):
        return None
    try:
        cd = int(float(raw))
    except (TypeError, ValueError):
        return None
    # Valid NYC CD codes: boro digit 1-5 + district 01-99
    return cd if 101 <= cd <= 599 else None


def _latest_value(rows: list, indicator: str) -> "float | None":
    """Latest data_value for one indicator, preferring annual averages.

    NYCCAS publishes 'Annual Average <year>' plus seasonal periods; pick
    the annual row with the newest start_date, falling back to the
    newest row of any period if no annual rows exist.
    """
    cands = []
    for r in rows:
        if r.get("name") != indicator or r.get("data_value") is None:
            continue
        try:
            val = float(r["data_value"])
        except (TypeError, ValueError):
            continue
        period = (r.get("time_period") or "")
        annual = 1 if period.lower().startswith("annual") else 0
        cands.append((annual, r.get("start_date") or "", val))
    if not cands:
        return None
    cands.sort()  # annual last within each, newest start_date last
    return round(cands[-1][2], 3)


def _absolute_score(index: "float | None") -> "float | None":
    """Health-anchored absolute score, clamped to [22, 92].

    Linear from index 8 (WHO-guideline-clean by NYC standards → 92) to
    index 20 (dirtiest NYC community districts → 22). The clamp encodes
    the honest truth that ALL of NYC sits far below EPA action levels:
    the dirtiest district reads "below average", never "hazardous", and
    no district earns a flawless score next to a six-lane avenue.
    """
    if index is None:
        return None
    frac = (_INDEX_WORST - float(index)) / (_INDEX_WORST - _INDEX_BEST)
    return round(22.0 + max(0.0, min(1.0, frac)) * 70.0, 1)
