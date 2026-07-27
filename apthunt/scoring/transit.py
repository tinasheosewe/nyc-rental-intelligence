"""
TransitScorer — scores listings by subway access and route diversity.

v2: uses exact street-entrance points (``ds_subway_entrances``) instead of
station centroids, adds a bus-route bonus (``ds_bus_stops``) and an ADA
signal (``ds_subway_stations``).  Falls back to the legacy TransitData /
stops.txt path when the entrances table is unavailable (e.g. still
downloading).

Scoring formula (absolute — see note on baselines below):
    d_eff       = d_nearest_entrance_m + severance_penalty_m
    proximity   = 100 * exp(-d_eff / 400)
    route_wt    = Σ over distinct routes within 800m of
                  (1.0 if route reaches the Manhattan CBD else 0.45)
    diversity   = min(1.0, route_wt / 6)
    subway      = proximity * (0.6 + 0.4 * diversity)
    bus_bonus   = min(10, 2 * distinct_bus_routes_within_300m)
    score       = min(100, subway + bus_bonus)

v3 nuances:
  * Line quality — a route counts fully toward diversity only if it
    reaches the Manhattan CBD (``ds_subway_stations.cbd``); shuttles and
    non-CBD lines (G, SIR — the only false ones, verified) count 0.45.
  * Severance — the nearest-entrance distance becomes an *effective*
    distance by adding ``path_severance_penalty_m`` (utils): an entrance
    200 m away across a six-lane arterial is not experientially 200 m.

Interpretation: an entrance at your door with 6+ CBD lines → ~100;
entrance at 400 m with 2 lines → ~27; nothing within 800 m → bus bonus
only.

Baseline choice: this scorer deliberately does NOT declare
``baseline_component`` / route through ``baseline_scores``.  The formula
is already absolute and self-calibrating — exp(-d/400) has meaningful
units (meters of walk), is defined for a single listing, and does not
drift with inventory.  Re-mapping it through a citywide percentile grid
would only distort the distance semantics (half the city scoring "50"
regardless of actual walk time) without adding reproducibility we don't
already have.

Components / columns:
    transit_station_count   INTEGER — stations with an entrance within 800 m
    transit_routes_served   INTEGER — distinct route letters across those stations
    transit_nearest_m       INTEGER — nearest street entrance (9999 = none in 800 m)
    transit_bus_routes      INTEGER — distinct bus routes within 300 m
    transit_ada_nearby      INTEGER — 1 if an ADA-accessible station within 800 m
    transit_cbd_routes      INTEGER — distinct CBD-reaching routes among those served
    transit_severance_m     INTEGER — pedestrian-severance penalty (effective extra
                                      meters) on the path to the nearest entrance
"""

from __future__ import annotations

import logging
import math
import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.data.transit_data import TransitData, _haversine

from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import path_severance_penalty_m

log = logging.getLogger(__name__)

CACHE_KEY = "transit_v3"  # v3: CBD line-quality weighting + severance-effective distance
LEGACY_CACHE_KEY = "transit"

SUBWAY_RADIUS_M = 800     # station/route catchment
BUS_RADIUS_M = 300        # bus stops must be genuinely close
PROXIMITY_SCALE_M = 400.0  # exp decay constant for entrance distance
NO_ENTRANCE_M = 9999      # sentinel kept from v1

CBD_ROUTE_WEIGHT = 1.0     # route reaches the Manhattan CBD
NON_CBD_ROUTE_WEIGHT = 0.45  # route never reaches the CBD (G, SIR)

# Verified against ds_subway_stations.cbd: G and SIR are the only routes
# with no CBD-flagged station.  Used as the fallback when the cbd column
# hasn't landed yet (re-downloads in flight).
NON_CBD_ROUTES_FALLBACK = frozenset({"G", "SIR"})


class TransitScorer(Scorer):
    """Subway + bus access scorer.

    Constructor keeps accepting ``TransitData`` (legacy fallback path).
    ``store`` is optional — when not injected, a ``DataStore`` is built
    lazily from the scoring connection.
    """

    def __init__(
        self,
        transit_data: TransitData,
        cache: BlockCache,
        store: DataStore | None = None,
    ):
        self._transit = transit_data
        self._cache = cache
        self._store = store

    @property
    def name(self) -> str:
        return "transit"

    def columns(self) -> dict[str, str]:
        return {
            "transit_station_count": "INTEGER",
            "transit_routes_served": "INTEGER",
            "transit_nearest_m": "INTEGER",
            "transit_bus_routes": "INTEGER",
            "transit_ada_nearby": "INTEGER",
            "transit_cbd_routes": "INTEGER",
            "transit_severance_m": "INTEGER",
        }

    # ------------------------------------------------------------------
    # Main scoring entry point
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        store = self._store or DataStore(conn)

        for ds in ("subway_entrances", "subway_stations", "bus_stops"):
            try:
                store.ensure_downloaded(ds, quiet=True)
            except Exception as exc:  # network hiccup — degrade, don't crash
                log.warning("transit: could not ensure dataset %s: %s", ds, exc)

        have_entrances = self._table_ready(conn, "ds_subway_entrances")
        have_stations = self._table_ready(conn, "ds_subway_stations")
        have_buses = self._table_ready(conn, "ds_bus_stops")

        if not have_entrances:
            log.info("transit: ds_subway_entrances unavailable — legacy path")
            return self._score_legacy(listings)

        # Probed ONCE per score() call — the cbd column may not have
        # landed yet (re-downloads in flight); falls back gracefully.
        non_cbd_routes = self._non_cbd_routes(store, have_stations)

        results: list[ScorerResult] = []
        for lst in listings:
            gh = lst["geohash"]
            stats = self._cache.get(gh, CACHE_KEY)
            if stats is None:
                stats = self._block_stats(
                    store, lst["lat"], lst["lon"], have_stations, have_buses,
                    non_cbd_routes,
                )
                self._cache.put(gh, CACHE_KEY, stats)

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=self._formula(stats),
                    components=dict(stats),
                )
            )
        return results

    # ------------------------------------------------------------------
    # v2 block stats + formula
    # ------------------------------------------------------------------

    def _block_stats(
        self,
        store: DataStore,
        lat: float,
        lon: float,
        have_stations: bool,
        have_buses: bool,
        non_cbd_routes: frozenset,
    ) -> dict:
        # --- Subway entrances within 800 m -----------------------------
        try:
            entrances = store.query_circle(
                "subway_entrances",
                lat=lat,
                lon=lon,
                radius_m=SUBWAY_RADIUS_M,
                select="station_id,daytime_routes,entrance_latitude,entrance_longitude",
                lat_col="entrance_latitude",
                lon_col="entrance_longitude",
            )
        except sqlite3.OperationalError:
            entrances = []

        station_ids: set = set()
        routes: set = set()
        nearest = NO_ENTRANCE_M
        nearest_coords: tuple | None = None
        for row in entrances:
            sid = row.get("station_id")
            if sid is not None:
                station_ids.add(str(sid))
            for r in (row.get("daytime_routes") or "").split():
                routes.add(r)
            d = row.get("_dist_m")
            if d is not None and int(d) < nearest:
                nearest = int(d)
                try:
                    nearest_coords = (
                        float(row["entrance_latitude"]),
                        float(row["entrance_longitude"]),
                    )
                except (KeyError, TypeError, ValueError):
                    nearest_coords = None

        # --- Severance on the walk to the nearest entrance -------------
        # Effective extra meters for crossing hostile roads; 0 when no
        # entrance was found or ds_roads is unavailable (graceful).
        severance = 0
        if nearest_coords is not None:
            try:
                severance = int(round(path_severance_penalty_m(
                    store, lat, lon, nearest_coords[0], nearest_coords[1],
                )))
            except Exception:
                severance = 0

        # --- CBD-reaching routes among those served --------------------
        cbd_routes = sum(1 for r in routes if r not in non_cbd_routes)

        # --- ADA station within 800 m ----------------------------------
        ada_nearby = 0
        if have_stations and station_ids:
            ada_nearby = self._ada_nearby(store, station_ids)

        # --- Bus routes within 300 m -----------------------------------
        bus_routes = 0
        if have_buses:
            bus_routes = self._bus_route_count(store, lat, lon)

        return {
            "transit_station_count": len(station_ids),
            "transit_routes_served": len(routes),
            "transit_nearest_m": nearest,
            "transit_bus_routes": bus_routes,
            "transit_ada_nearby": ada_nearby,
            "transit_cbd_routes": cbd_routes,
            "transit_severance_m": severance,
        }

    @staticmethod
    def _formula(stats: dict) -> float:
        nearest_raw = stats.get("transit_nearest_m")
        nearest = float(NO_ENTRANCE_M if nearest_raw is None else nearest_raw)
        n_routes = int(stats.get("transit_routes_served") or 0)
        bus_routes = int(stats.get("transit_bus_routes") or 0)

        # Effective distance: raw walk + pedestrian-severance penalty.
        severance = float(stats.get("transit_severance_m") or 0)
        d_eff = min(float(NO_ENTRANCE_M), nearest + severance)

        # Line-quality-weighted diversity: CBD-reaching routes count
        # fully, non-CBD routes (G, SIR) at NON_CBD_ROUTE_WEIGHT.  When
        # the component is absent (stale cache row / legacy), fall back
        # to the unweighted count.
        cbd_raw = stats.get("transit_cbd_routes")
        if cbd_raw is None:
            weighted_routes = float(n_routes)
        else:
            cbd = max(0, min(int(cbd_raw), n_routes))
            weighted_routes = (
                CBD_ROUTE_WEIGHT * cbd
                + NON_CBD_ROUTE_WEIGHT * (n_routes - cbd)
            )

        proximity = 100.0 * math.exp(-d_eff / PROXIMITY_SCALE_M)
        diversity = min(1.0, weighted_routes / 6.0)
        subway = proximity * (0.6 + 0.4 * diversity)
        bus_bonus = min(10.0, 2.0 * bus_routes)
        return round(max(0.0, min(100.0, subway + bus_bonus)), 1)

    def _non_cbd_routes(self, store: DataStore, have_stations: bool) -> frozenset:
        """Routes that never reach the Manhattan CBD, per ds_subway_stations.cbd.

        Derived from data so route changes propagate on re-download; the
        cbd column is part of an in-flight re-download, so any failure
        (missing table/column, thin data) falls back to the verified
        static set {G, SIR}.
        """
        if have_stations:
            try:
                rows = store.query(
                    "subway_stations", select="daytime_routes,cbd",
                )
            except sqlite3.OperationalError:
                rows = []
            all_routes: set = set()
            cbd_routes: set = set()
            for r in rows:
                route_list = (r.get("daytime_routes") or "").split()
                all_routes.update(route_list)
                if str(r.get("cbd") or "").strip().upper() in (
                    "1", "TRUE", "T", "Y", "YES",
                ):
                    cbd_routes.update(route_list)
            if cbd_routes:
                return frozenset(all_routes - cbd_routes)
        return NON_CBD_ROUTES_FALLBACK

    def _ada_nearby(self, store: DataStore, station_ids: set) -> int:
        """1 if any of the given stations is ADA-accessible (full or partial)."""
        ids = sorted(station_ids)
        placeholders = ", ".join("?" for _ in ids)
        try:
            rows = store.query(
                "subway_stations",
                where_clause=f"station_id IN ({placeholders})",
                params=tuple(ids),
                select="ada",
            )
        except sqlite3.OperationalError:
            return 0
        for r in rows:
            try:
                if int(float(r.get("ada") or 0)) >= 1:
                    return 1
            except (ValueError, TypeError):
                continue
        return 0

    def _bus_route_count(self, store: DataStore, lat: float, lon: float) -> int:
        """Distinct bus routes across deduped stops within BUS_RADIUS_M."""
        try:
            rows = store.query_circle(
                "bus_stops",
                lat=lat,
                lon=lon,
                radius_m=BUS_RADIUS_M,
                select="routes",
            )
        except sqlite3.OperationalError:
            return 0
        distinct: set = set()
        for r in rows:
            for route in (r.get("routes") or "").split(","):
                route = route.strip()
                if route:
                    distinct.add(route)
        return len(distinct)

    @staticmethod
    def _table_ready(conn: sqlite3.Connection, table: str) -> bool:
        try:
            conn.execute(f"SELECT 1 FROM [{table}] LIMIT 1")
            return True
        except sqlite3.OperationalError:
            return False

    # ------------------------------------------------------------------
    # Legacy fallback: TransitData / stops.txt (v1 behavior, kept intact)
    # ------------------------------------------------------------------

    def _score_legacy(self, listings: list[dict]) -> list[ScorerResult]:
        results: list[ScorerResult] = []
        for lst in listings:
            gh = lst["geohash"]
            cached = self._cache.get(gh, LEGACY_CACHE_KEY)
            if cached is not None:
                station_count = cached["station_count"]
                routes = cached["routes_served"]
                nearest = cached["nearest_m"]
            else:
                lat, lon = lst["lat"], lst["lon"]
                stations = self._transit.stations_within(lat, lon, SUBWAY_RADIUS_M)
                station_count = len(stations)
                routes = len(set(r for s in stations for r in s.routes))
                if stations:
                    nearest = int(
                        min(_haversine(lat, lon, s.lat, s.lon) for s in stations)
                    )
                else:
                    nearest = NO_ENTRANCE_M
                self._cache.put(
                    gh,
                    LEGACY_CACHE_KEY,
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
                        "transit_bus_routes": None,
                        "transit_ada_nearby": None,
                        "transit_cbd_routes": None,
                        "transit_severance_m": None,
                    },
                )
            )
        return results
