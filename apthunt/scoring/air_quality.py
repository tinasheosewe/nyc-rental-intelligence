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

        air_quality_index = pm25 + 0.3 * no2_local

    which puts the two pollutants on a comparable scale (NYC annual
    PM2.5 ≈ 6–9 µg/m³, NO2 ≈ 12–30 ppb, so 0.3·NO2 ≈ 3.6–9).

    Lower is better (``reverse=True``); zero is NOT perfect — zero
    ambient air pollution does not exist, so no ``zero_is_perfect`` pin.

Road-gradient micro-adjustment (breaks the 58-value CD degeneracy):
    CD-level surfaces give every listing in a community district the
    same NO2 — but NO2 rises measurably near highways *within* a CD.
    We nudge the CD value by the listing's road exposure relative to
    its CD's typical road exposure, mean-preserving by construction:

        NO2_local = NO2_cd + clip(beta * (road_idx − cd_mean_road_idx),
                                  ±NO2_ROAD_CLIP_PPB)

    with ``beta = 0.15`` ppb per road-index unit and a ±3 ppb clip
    (road_exposure_index spans ~0–16, so the un-clipped swing is
    ~±2.3 ppb for extreme within-CD contrasts). The CD mean is taken
    over **active listings** in that CD and cached in-process.

    ``road_exposure_index`` arrives on the listing dict itself — the
    engine loads listings with SELECT *, so the key is present once
    the RoadExposureScorer has run (values reflect the previous
    engine run within a single pass; road exposure is static per
    location so a one-run lag is immaterial). When the column is
    absent (re-download / first run) or NULL, the scorer degrades
    gracefully to the CD-flat value.

Output columns:
    air_quality_pm25       REAL — latest annual PM2.5 for the CD (µg/m³)
    air_quality_no2        REAL — latest annual NO2 for the CD (ppb)
    air_quality_no2_local  REAL — road-gradient-adjusted NO2 (ppb)
    air_quality_road_adj   REAL — applied NO2 adjustment (ppb, ±clip;
                                  NULL when degraded to CD-flat)
    air_quality_index      REAL — pm25 + 0.3 * no2_local (baseline
                                  metric; rebaseline follows this wave)

Robustness:
    ``ds_pluto.cd`` only exists after a re-download of PLUTO, and
    ``ds_air_quality`` may still be downloading.  A missing table OR a
    missing ``cd`` column is probed up front; either one makes the
    scorer return score=None / components={} for every listing.
    The ``listings.road_exposure_index`` column is probed with a
    try/except once per score() call — missing column means CD-flat
    scoring, never a crash.

Block cache: ``air_quality_v2`` (v1 cached the CD-flat combined index;
v2 caches CD identity + per-pollutant CD values, with the local index
computed per listing).
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from apthunt import geohash as _geohash
from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import dedupe_by_geohash, find_nearest_row

# NYCCAS indicator names (exact strings in ds_air_quality.name)
_PM25 = "Fine particles (PM 2.5)"
_NO2 = "Nitrogen dioxide (NO2)"

# NO2 weight in the combined index (ppb → comparable to µg/m³ PM2.5)
NO2_WEIGHT = 0.3

# Road-gradient micro-adjustment: ppb of NO2 per road-index unit of
# within-CD deviation, and the hard clip on the applied adjustment.
NO2_ROAD_BETA = 0.15
NO2_ROAD_CLIP_PPB = 3.0

# Block-cache version key. v2: stats carry _cd + CD-level pollutant
# values only; the combined index moved to per-listing computation
# (road-gradient NO2 adjustment — semantics change).
_CACHE_KEY = "air_quality_v2"

# Absolute fallback anchors (used only before the first baseline build):
# index ≈ 8  → WHO-guideline-clean by NYC standards (PM2.5 ~5 + 0.3·NO2 ~10)
# index ≈ 20 → the dirtiest NYC CDs (Midtown-class PM2.5 ~9 + NO2 ~35)
_INDEX_BEST = 8.0
_INDEX_WORST = 20.0


class AirQualityScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache
        # cd -> {"air_quality_pm25", "air_quality_no2"} memo (in-process)
        self._cd_metrics_memo: dict = {}
        # cd -> mean road_exposure_index over active listings (in-process;
        # None = not built yet, {} is a valid "built, nothing found" state)
        self._cd_road_means: Optional[dict] = None

    @property
    def name(self) -> str:
        return "air_quality"

    # Citywide baseline metric (sampled by scripts/build_baseline.py).
    # NB: the raw definition now includes the road-gradient NO2 term —
    # a rebaseline follows this wave.
    baseline_component = "air_quality_index"
    baseline_reverse = True
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "air_quality_pm25": "REAL",
            "air_quality_no2": "REAL",
            "air_quality_no2_local": "REAL",
            "air_quality_road_adj": "REAL",
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
        block_stats: dict = {}
        for gh, (lat, lon) in gh_map.items():
            block_stats[gh] = self._resolve_block(gh, lat, lon)

        # Per-CD mean road exposure over active listings (in-process
        # cache; {} whenever listings.road_exposure_index is missing —
        # probed per call — which degrades everything to CD-flat).
        cd_road_means = self._cd_road_mean_map(conn)

        # DELIBERATELY ABSOLUTE, not percentile: NYC's intra-city air
        # differences are modest in health terms (PM2.5 ~6-11 µg/m³ —
        # all far below EPA action levels). Percentiling that narrow
        # range manufactured drama ("worse than 95% of NYC") for
        # differences no resident can perceive. Anchored to health
        # guidance instead: the worst NYC districts read "below
        # average", not "hazardous".
        results: list[ScorerResult] = []
        for lst in listings:
            stats = block_stats[lst["geohash"]]
            pm25 = stats["air_quality_pm25"]
            no2_cd = stats["air_quality_no2"]
            cd = stats.get("_cd")

            # --- road-gradient NO2 micro-adjustment (mean-preserving) --
            road_adj = None
            no2_local = no2_cd
            if no2_cd is not None and cd is not None:
                road_idx = lst.get("road_exposure_index")
                cd_mean = cd_road_means.get(cd)
                if road_idx is not None and cd_mean is not None:
                    try:
                        delta = float(road_idx) - cd_mean
                        adj = NO2_ROAD_BETA * delta
                        adj = max(-NO2_ROAD_CLIP_PPB,
                                  min(NO2_ROAD_CLIP_PPB, adj))
                        road_adj = round(adj, 3)
                        no2_local = round(no2_cd + road_adj, 3)
                    except (TypeError, ValueError):
                        road_adj = None
                        no2_local = no2_cd

            index = None
            if pm25 is not None and no2_local is not None:
                index = round(pm25 + NO2_WEIGHT * no2_local, 3)
            elif pm25 is not None:
                index = round(pm25, 3)  # degrade gracefully to PM2.5-only

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=_absolute_score(index),
                    components={
                        "air_quality_pm25": pm25,
                        "air_quality_no2": no2_cd,
                        "air_quality_no2_local": no2_local,
                        "air_quality_road_adj": road_adj,
                        "air_quality_index": index,
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_block(self, gh: str, lat: float, lon: float) -> dict:
        """CD identity + CD-level pollutant values for one block
        (block-cached under ``air_quality_v2``)."""
        cached = self._cache.get(gh, _CACHE_KEY)
        if cached is not None:
            return cached

        # --- nearest PLUTO lot → community district -------------------
        pluto_rows = self._store.query_bbox(
            "pluto", lat, lon, delta=0.0015,
            select="latitude,longitude,cd",
        )
        nearest = find_nearest_row(pluto_rows, lat, lon)
        cd = _norm_cd(nearest.get("cd")) if nearest else None

        if cd is None:
            stats = {
                "_cd": None,
                "air_quality_pm25": None,
                "air_quality_no2": None,
            }
        else:
            if cd not in self._cd_metrics_memo:
                self._cd_metrics_memo[cd] = self._cd_metrics(cd)
            m = self._cd_metrics_memo[cd]
            stats = {
                "_cd": cd,
                "air_quality_pm25": m["air_quality_pm25"],
                "air_quality_no2": m["air_quality_no2"],
            }

        self._cache.put(gh, _CACHE_KEY, stats)
        return stats

    def _cd_road_mean_map(self, conn: sqlite3.Connection) -> dict:
        """cd -> mean road_exposure_index over active listings.

        Built once per process (cached on the instance) from the
        listings table; each active listing's CD is resolved through
        the same block cache the main loop warms, so the marginal cost
        after the first batch is a single SQL query + dict lookups.

        The ``road_exposure_index`` column may not exist yet (wave
        re-downloads in flight): the query is probed with try/except on
        EVERY call until it succeeds — a missing column returns {}
        WITHOUT caching, so the scorer degrades to CD-flat now and
        picks the column up as soon as it lands.
        """
        if self._cd_road_means is not None:
            return self._cd_road_means
        try:
            rows = conn.execute(
                "SELECT lat, lon, road_exposure_index FROM listings "
                "WHERE UPPER(status) = 'ACTIVE' "
                "AND lat IS NOT NULL AND lon IS NOT NULL "
                "AND road_exposure_index IS NOT NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}  # column not landed yet — re-probe next call

        sums: dict = {}
        for row in rows:
            try:
                lat = float(row[0])
                lon = float(row[1])
                road_idx = float(row[2])
            except (TypeError, ValueError):
                continue
            gh = _geohash.encode(lat, lon)
            cd = self._resolve_block(gh, lat, lon).get("_cd")
            if cd is None:
                continue
            s, c = sums.get(cd, (0.0, 0))
            sums[cd] = (s + road_idx, c + 1)

        self._cd_road_means = {
            cd: round(s / c, 4) for cd, (s, c) in sums.items()
        }
        return self._cd_road_means

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
        return {
            "air_quality_pm25": _latest_value(rows, _PM25),
            "air_quality_no2": _latest_value(rows, _NO2),
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

    The road-gradient NO2 term shifts the index by at most ±0.9
    (0.3 × 3 ppb clip) → at most ±5.3 score points of within-CD
    differentiation, mean-preserving per CD.
    """
    if index is None:
        return None
    frac = (_INDEX_WORST - float(index)) / (_INDEX_WORST - _INDEX_BEST)
    return round(22.0 + max(0.0, min(1.0, frac)) * 70.0, 1)
