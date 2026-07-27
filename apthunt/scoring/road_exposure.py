"""
RoadExposureScorer — scores listings by traffic exposure from major roads.

Data sources
------------
1. ``ds_roads`` — OSM major-road ways (motorway → tertiary) downloaded
   via Overpass and sampled into points every ~50 m along each polyline
   (see ``DataStore._overpass_roads_download``).
2. ``ds_truck_routes`` — NYC DOT designated truck routes (SODA
   jjja-shxy), line geometries sampled into ~50 m points at download
   time (post-process ``sample_truck_routes``).

Method
------
Three additive components form a raw exposure index (higher = worse):

**Highway proximity** — distance ``d`` to the nearest
motorway/motorway_link/trunk/trunk_link sampled point (searched within
1 km)::

    exposure_hwy = 10 * exp(-d / 250)

i.e. living on top of an expressway ≈ 10, 250 m away ≈ 3.7, ~1 km away
≈ 0.2 (and 0 when none within the search radius).

**Arterial density** — every road point within 150 m contributes its
class weight through a Gaussian distance kernel::

    arterial_sum = Σ CLASS_WEIGHTS[class] * exp(-(d / 75)**2)
    arterial     = arterial_sum / POINTS_PER_100M          # = sum / 2

Normalization math: each sampled point stands for ~50 m of roadway
(SAMPLE_SPACING_M), so a 100 m stretch of road contributes
100/50 = 2 points (POINTS_PER_100M).  Dividing the kernel-weighted sum
by that factor expresses the metric in *class-weight units per 100 m of
roadway* — i.e. one full-kernel 100 m block face of a secondary road
contributes ~3.0 — and makes the index invariant to the sampling
interval (resampling at 25 m would double the points and the raw sum,
but not the normalized density).

**Truck route** — distance ``d`` to the nearest designated truck-route
point, only within 100 m::

    truck_bonus = 3 * exp(-d / 60)      (0 beyond 100 m)

**Tunnel / bridge awareness** (needs the re-downloaded ``ds_roads``
with ``tunnel``/``bridge`` INTEGER columns — degrades to the old
behaviour when absent):

* Tunnel points contribute ZERO to both the highway and arterial
  components — a motorway under bedrock is inaudible at the surface
  (~700 tunnel points exist: Lincoln/QMT/Holland/Park Ave etc).
* Bridge/viaduct points SKIP occlusion attenuation — an elevated
  source clears the first building row, so shield rows don't apply
  (neither the highway occlusion factors nor the arterial midpoint
  shield probe).

**Bus trunk corridor** — nearest ``ds_bus_stops`` stop within 120 m;
a stop served by many routes marks a trunk corridor (idling, pull-outs,
brakes)::

    bus_bonus = min(4.0, 0.35 * route_count) * exp(-(d / 60)**2)

**Firehouse** — nearest ``ds_firehouses`` house within 150 m (siren
corridor; dataset optional — degrades to 0 while downloading)::

    firehouse_bonus = 2.5 * exp(-d / 80)

Raw metric (baseline component)::

    road_exposure_index = exposure_hwy + arterial + truck_bonus
                          + el_bonus + bus_bonus + firehouse_bonus

The index is scored against the frozen citywide baseline with
``reverse=True`` (more exposure = worse) and ``zero_perfect=False`` —
an inner block still has SOME exposure (a tertiary street two blocks
over registers weakly); an index of exactly zero only means "far from
all major roads", which is not categorically perfect the way "zero
bedbug reports" is, so it takes whatever percentile the distribution
assigns rather than a pinned 100.

Fallback (documented): until the first ``build_baseline.py`` run there
is no citywide distribution, so an absolute linear formula is used::

    score = max(0, 100 - 6 * road_exposure_index)

index 0 (no majors nearby) → 100; ~4 (one arterial block face) → 76;
~8 (busy multi-arterial corner) → 52; ≥ 16.7 (expressway-adjacent) → 0.

Output columns:
    road_exposure_hwy_dist_m REAL — metres to nearest motorway/trunk
                                    point (NULL when none within 1 km)
    road_exposure_arterial   REAL — kernel-weighted class-weight density
                                    per 100 m of roadway (150 m radius)
    road_exposure_truck      REAL — truck-route proximity bonus (0–3)
    road_exposure_bus        REAL — bus trunk-corridor bonus (0–4)
    road_exposure_firehouse_m REAL — metres to nearest firehouse
                                    (NULL when none within 150 m)
    road_exposure_index      REAL — combined raw exposure (baseline metric)
"""

from __future__ import annotations

import logging
import math
import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores
from apthunt.scoring.utils import dedupe_by_geohash

log = logging.getLogger(__name__)


# Relative nuisance weight of each OSM highway class (traffic volume,
# noise, and pollution proxy). Links (ramps) sit between their parent
# class and the next tier down.
CLASS_WEIGHTS = {
    "motorway": 10.0,
    "motorway_link": 8.0,
    "trunk": 8.0,
    "trunk_link": 6.0,
    "primary": 5.0,
    "secondary": 3.0,
    "tertiary": 1.5,
}

# Highway-proximity component: classes counted as "a highway", the
# search radius, and the exponential decay scale.
HWY_CLASSES = frozenset(
    {"motorway", "motorway_link", "trunk", "trunk_link"}
)
HWY_SEARCH_RADIUS_M = 1000.0   # exp(-1000/250) ≈ 0.02 — negligible beyond
HWY_DECAY_M = 250.0
HWY_MAX_EXPOSURE = 10.0

# Arterial-density component.
ARTERIAL_RADIUS_M = 150.0
ARTERIAL_SIGMA_M = 75.0
SAMPLE_SPACING_M = 50.0        # must match DataStore.ROAD_SAMPLE_SPACING_M
POINTS_PER_100M = 100.0 / SAMPLE_SPACING_M   # = 2.0 — see module docstring

# Truck-route component.
TRUCK_RADIUS_M = 100.0
TRUCK_DECAY_M = 60.0

# Bus trunk-corridor component: a stop served by many routes marks a
# trunk corridor (idling, pull-outs, brakes). Nearest stop only.
BUS_RADIUS_M = 120.0
BUS_ROUTE_WEIGHT = 0.35
BUS_MAX_BONUS = 4.0
BUS_SIGMA_M = 60.0

# Firehouse component (siren corridor).
FIREHOUSE_RADIUS_M = 150.0
FIREHOUSE_MAX_BONUS = 2.5
FIREHOUSE_DECAY_M = 80.0

# Elevated-train component: search radius, peak contribution, decay.
# Decay (300m) deliberately exceeds track audibility (~150m) to
# compensate for station points sampling the track every ~500-800m.
EL_RADIUS_M = 800
EL_MAX_BONUS = 6.0
EL_DECAY_M = 300.0

# Acoustic shielding ("buildings in between"): attenuation per intervening
# 3+-story building row on the sightline to a long-range source
# (highway / elevated track). One solid row ~ -7 dBA perceived (x0.45),
# two or more rows x0.2. Arterial points beyond the first building row
# (FRONTAGE_M) get a single midpoint occlusion probe (x0.35 when blocked).
OCCLUSION_FACTORS = (1.0, 0.45, 0.2)
OCCLUDER_MIN_FLOORS = 3.0
OCCLUDER_PROBE_M = 18.0
FRONTAGE_M = 60.0
ARTERIAL_SHIELD_FACTOR = 0.35
TRUCK_MAX_BONUS = 3.0

# Absolute fallback until the first baseline build (see module docstring).
FALLBACK_SLOPE = 6.0

# Version bumped v3→v4: tunnel/bridge handling, bus corridor, firehouse.
# The availability flags of the still-downloading dependencies are folded
# into the key at score() time (see _cache_key), so blocks computed while
# a table/column is missing are recomputed automatically once it lands.
_CACHE_VERSION = "road_exposure_v5"


class RoadExposureScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "road_exposure"

    # Citywide baseline metric (sampled by scripts/build_baseline.py).
    # reverse: more exposure = worse. zero_perfect False: an index of 0
    # just means "far from majors", not a categorically perfect block.
    baseline_component = "road_exposure_index"
    baseline_reverse = True
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "road_exposure_hwy_dist_m": "REAL",
            "road_exposure_arterial": "REAL",
            "road_exposure_truck": "REAL",
            "road_exposure_el_dist_m": "REAL",
            "road_exposure_hwy_shield_rows": "INTEGER",
            "road_exposure_el": "REAL",
            "road_exposure_bus": "REAL",
            "road_exposure_firehouse_m": "REAL",
            "road_exposure_index": "REAL",
        }

    # ------------------------------------------------------------------
    # Dependency probes (new columns/tables may still be downloading)
    # ------------------------------------------------------------------

    def _has(self, dataset: str, col: str) -> bool:
        """True when ds_<dataset> exists AND exposes *col* — probed once
        per score() call so the scorer runs against today's DB while the
        nuance-wave re-downloads are still in flight."""
        try:
            self._store.query(dataset, select=col, limit=1)
            return True
        except sqlite3.OperationalError:
            return False

    # ------------------------------------------------------------------
    # Main scoring entry point
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        # Ensure datasets exist; tolerate download failure (Overpass or
        # SODA down, or the table mid-download in another process) — the
        # probe below degrades to score None rather than crashing the run.
        for ds in ("roads", "truck_routes", "bus_stops", "firehouses"):
            try:
                self._store.ensure_downloaded(ds, quiet=True)
            except Exception as exc:
                log.warning("road_exposure: could not ensure %s: %s", ds, exc)

        # Missing-table tolerance: if either table is still absent, this
        # dimension is unknown for the whole batch — score None for all.
        try:
            self._store.query("roads", select="way_id", limit=1)
            self._store.query("truck_routes", select="route_type", limit=1)
        except sqlite3.OperationalError:
            return [
                ScorerResult(listing_id=lst["id"], score=None, components={})
                for lst in listings
            ]

        # Probe still-downloading dependencies once per call; fold their
        # availability into the cache key so blocks scored while a
        # table/column is missing recompute once the re-download lands.
        has_tb = self._has("roads", "tunnel,bridge")
        has_bus = self._has("bus_stops", "route_count")
        has_fh = self._has("firehouses", "latitude")
        cache_key = "%s:t%db%df%d" % (
            _CACHE_VERSION, int(has_tb), int(has_bus), int(has_fh)
        )

        # Deduplicate by geohash (one lookup per block)
        gh_map = dedupe_by_geohash(listings)

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, cache_key)
            if cached is not None:
                block_stats[gh] = cached
                continue
            stats = self._block_exposure(lat, lon, has_tb, has_bus, has_fh)
            block_stats[gh] = stats
            self._cache.put(gh, cache_key, stats)

        # Score the raw index against the frozen citywide baseline
        # (lower = better; no zero-is-perfect pinning).
        raw = [
            block_stats[lst["geohash"]]["road_exposure_index"]
            for lst in listings
        ]
        scores = baseline_scores(conn, self.name, raw, reverse=True)
        if scores is None:
            # Absolute fallback until the first baseline build:
            # 100 - 6*index (floored at 0) — defined for a single
            # listing, no batch-relative artifacts.
            scores = [
                round(max(0.0, 100.0 - FALLBACK_SLOPE * float(v)), 1)
                for v in raw
            ]

        results: list[ScorerResult] = []
        for lst, sc in zip(listings, scores):
            stats = block_stats[lst["geohash"]]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=sc,
                    components={
                        "road_exposure_hwy_dist_m": stats[
                            "road_exposure_hwy_dist_m"
                        ],
                        "road_exposure_arterial": stats[
                            "road_exposure_arterial"
                        ],
                        "road_exposure_truck": stats["road_exposure_truck"],
                        "road_exposure_el_dist_m": stats.get("road_exposure_el_dist_m"),
                        "road_exposure_hwy_shield_rows": stats.get("road_exposure_hwy_shield_rows", 0),
                        "road_exposure_el": stats.get("road_exposure_el", 0.0),
                        "road_exposure_bus": stats.get("road_exposure_bus", 0.0),
                        "road_exposure_firehouse_m": stats.get("road_exposure_firehouse_m"),
                        "road_exposure_index": stats["road_exposure_index"],
                    },
                )
            )
        return results


    # ── Sightline occlusion helpers ────────────────────────────────

    def _lots_blocking(self, plat: float, plon: float) -> bool:
        """Any 3+-story PLUTO lot within OCCLUDER_PROBE_M of a point?"""
        try:
            rows = self._store.query_circle(
                "pluto", lat=plat, lon=plon, radius_m=OCCLUDER_PROBE_M,
                select="numfloors",
            )
        except sqlite3.OperationalError:
            return False
        for r in rows:
            try:
                if float(r.get("numfloors") or 0) >= OCCLUDER_MIN_FLOORS:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _blocked_midpoint(self, lat: float, lon: float,
                          plat: float, plon: float) -> bool:
        """Single-probe occlusion check at the sightline midpoint."""
        return self._lots_blocking((lat + plat) / 2.0, (lon + plon) / 2.0)

    def _occluding_rows(self, lat: float, lon: float,
                        slat: float, slon: float) -> int:
        """Count intervening building rows on the listing→source sightline.

        Samples the segment every ~25m (excluding ~20m buffers at both
        ends so the source's and listing's own buildings don't count).
        Consecutive blocked samples merge into one "row".

        Fast path: when a BlockerRaster is attached to the store
        (DataStore.attach_fast_path), the walk runs as O(1) raster
        lookups instead of ~25 SQLite circle queries; any raster failure
        degrades to the SQLite loop below.
        """
        raster = getattr(self._store, "blocker_raster", None)
        if raster is not None:
            try:
                return raster.occluding_rows(lat, lon, slat, slon)
            except Exception:
                pass  # degrade to the SQLite reference path
        import math as _m
        dy = (slat - lat) * 111_320.0
        dx = (slon - lon) * 85_000.0
        seg = _m.hypot(dx, dy)
        if seg <= 45.0:
            return 0  # too close for an intervening row
        n = max(1, int(seg / 25.0))
        rows = 0
        in_row = False
        for i in range(1, n):
            f = i / n
            d_here = f * seg
            if d_here < 20.0 or (seg - d_here) < 20.0:
                continue
            blocked = self._lots_blocking(lat + f * (slat - lat),
                                          lon + f * (slon - lon))
            if blocked and not in_row:
                rows += 1
                in_row = True
            elif not blocked:
                in_row = False
        return rows

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _block_exposure(
        self,
        lat: float,
        lon: float,
        has_tb: bool = False,
        has_bus: bool = False,
        has_fh: bool = False,
    ) -> dict:
        """Compute the exposure components for one block.

        ``has_tb``/``has_bus``/``has_fh``: probed availability of the
        tunnel/bridge road columns, ds_bus_stops, and ds_firehouses —
        components degrade to their pre-nuance behaviour (or 0) when the
        corresponding data hasn't finished downloading.
        """
        # One circle query serves both the highway-proximity search
        # (1 km) and the arterial-density sum (points ≤ 150 m).
        road_select = "road_class,lat,lon"
        if has_tb:
            road_select += ",tunnel,bridge"
        road_rows = self._store.query_circle(
            "roads", lat=lat, lon=lon, radius_m=HWY_SEARCH_RADIUS_M,
            select=road_select, lat_col="lat", lon_col="lon",
        )

        hwy_dist = None
        hwy_pt = None
        hwy_bridge = False
        arterial_sum = 0.0
        for r in road_rows:
            cls = r.get("road_class") or ""
            d = float(r.get("_dist_m") or 0.0)
            # Tunnel points are inaudible at the surface (Lincoln/QMT/
            # Holland/Park Ave etc) — zero contribution to everything.
            if has_tb and (r.get("tunnel") or 0):
                continue
            bridge = bool(r.get("bridge") or 0) if has_tb else False
            if cls in HWY_CLASSES:
                if hwy_dist is None or d < hwy_dist:
                    hwy_dist = d
                    hwy_pt = (float(r.get("lat")), float(r.get("lon")))
                    hwy_bridge = bridge
                # Highways are modeled by the (occlusion-aware) highway
                # component — counting their points in the arterial field
                # too double-counted them at close range and made a
                # BQE-adjacent-but-shielded block look like an arterial
                # canyon.
                continue
            w = CLASS_WEIGHTS.get(cls)
            if w is not None and d <= ARTERIAL_RADIUS_M:
                k = math.exp(-((d / ARTERIAL_SIGMA_M) ** 2))
                # Street-canyon shielding for the far arterial field:
                # beyond FRONTAGE_M the sound has to cross the first
                # building row — a single midpoint probe for a 3+-story
                # intervening lot approximates that. Elevated (bridge/
                # viaduct) points clear the first building row, so they
                # skip the shield probe.
                if d > FRONTAGE_M and not bridge:
                    try:
                        plat, plon = float(r.get("lat")), float(r.get("lon"))
                        if self._blocked_midpoint(lat, lon, plat, plon):
                            k *= ARTERIAL_SHIELD_FACTOR
                    except (TypeError, ValueError):
                        pass
                arterial_sum += w * k
        # Per-100m-of-roadway normalization — see module docstring.
        arterial = arterial_sum / POINTS_PER_100M

        exposure_hwy = 0.0
        hwy_shield_rows = 0
        if hwy_dist is not None:
            exposure_hwy = HWY_MAX_EXPOSURE * math.exp(-hwy_dist / HWY_DECAY_M)
            # Sightline occlusion: intervening 3+-story building rows
            # between the listing and the highway attenuate hard (a solid
            # row cuts traffic noise ~10-20 dBA — the "buildings in
            # between" effect). Bridge/viaduct sources are elevated and
            # clear the first building row — full exposure, no occlusion
            # (shield rows not computed; reported as 0).
            if not hwy_bridge:
                hwy_shield_rows = self._occluding_rows(lat, lon, *hwy_pt)
                exposure_hwy *= OCCLUSION_FACTORS[min(hwy_shield_rows, 2)]

        truck_rows = self._store.query_circle(
            "truck_routes", lat=lat, lon=lon, radius_m=TRUCK_RADIUS_M,
            select="route_type", lat_col="lat", lon_col="lon",
        )
        truck_bonus = 0.0
        if truck_rows:
            td = min(float(r.get("_dist_m") or 0.0) for r in truck_rows)
            truck_bonus = TRUCK_MAX_BONUS * math.exp(-td / TRUCK_DECAY_M)

        # Elevated trains are as loud as any arterial (the J/M/Z over
        # Broadway, the 7 over Roosevelt). Stations point-sample the
        # elevated TRACK (~500-800m apart), so mid-track blocks sit up to
        # ~300-400m from the nearest station point — the decay constant
        # (300m) is deliberately longer than track audibility (~150m) to
        # compensate for that sampling sparsity. Missing table (dataset
        # optional) → component 0.
        el_bonus = 0.0
        el_dist = None
        try:
            el_rows = self._store.query_circle(
                "subway_stations", lat=lat, lon=lon, radius_m=EL_RADIUS_M,
                select="structure,gtfs_latitude,gtfs_longitude", lat_col="gtfs_latitude",
                lon_col="gtfs_longitude",
            )
            el_pts = [
                (float(r.get("_dist_m") or 0.0),
                 float(r.get("gtfs_latitude")), float(r.get("gtfs_longitude")))
                for r in el_rows
                if (r.get("structure") or "") == "Elevated"
            ]
            if el_pts:
                el_dist, ela, elo = min(el_pts)
                el_bonus = EL_MAX_BONUS * math.exp(-el_dist / EL_DECAY_M)
                # Same sightline occlusion as the highway component —
                # a building row between the listing and the elevated
                # track shields it.
                rows_blk = self._occluding_rows(lat, lon, ela, elo)
                el_bonus *= OCCLUSION_FACTORS[min(rows_blk, 2)]
        except sqlite3.OperationalError:
            pass  # stations table absent — skip the component

        # Bus trunk corridor: the nearest stop's route count proxies
        # corridor intensity (an M15-SBS trunk stop ≠ a one-route stop).
        bus_bonus = 0.0
        if has_bus:
            try:
                bus_rows = self._store.query_circle(
                    "bus_stops", lat=lat, lon=lon, radius_m=BUS_RADIUS_M,
                    select="route_count,latitude,longitude",
                )
                if bus_rows:
                    nearest = min(
                        bus_rows, key=lambda r: float(r.get("_dist_m") or 0.0)
                    )
                    bd = float(nearest.get("_dist_m") or 0.0)
                    try:
                        rc = float(nearest.get("route_count") or 0)
                    except (TypeError, ValueError):
                        rc = 0.0
                    bus_bonus = (
                        min(BUS_MAX_BONUS, BUS_ROUTE_WEIGHT * rc)
                        * math.exp(-((bd / BUS_SIGMA_M) ** 2))
                    )
            except sqlite3.OperationalError:
                pass  # table dropped mid-run — skip the component

        # Firehouse: siren corridor. Dataset optional (mid-download) —
        # component 0 / distance NULL until it lands.
        fh_bonus = 0.0
        fh_dist = None
        if has_fh:
            try:
                fh_rows = self._store.query_circle(
                    "firehouses", lat=lat, lon=lon,
                    radius_m=FIREHOUSE_RADIUS_M,
                    select="latitude,longitude",
                )
                if fh_rows:
                    fh_dist = min(
                        float(r.get("_dist_m") or 0.0) for r in fh_rows
                    )
                    fh_bonus = FIREHOUSE_MAX_BONUS * math.exp(
                        -fh_dist / FIREHOUSE_DECAY_M
                    )
            except sqlite3.OperationalError:
                pass  # table dropped mid-run — skip the component

        index = (
            exposure_hwy + arterial + truck_bonus + el_bonus
            + bus_bonus + fh_bonus
        )
        return {
            "road_exposure_hwy_dist_m": (
                round(hwy_dist, 1) if hwy_dist is not None else None
            ),
            "road_exposure_arterial": round(arterial, 4),
            "road_exposure_truck": round(truck_bonus, 4),
            "road_exposure_el_dist_m": (
                round(el_dist, 1) if el_dist is not None else None
            ),
            "road_exposure_hwy_shield_rows": hwy_shield_rows,
            "road_exposure_el": round(el_bonus, 4),
            "road_exposure_bus": round(bus_bonus, 4),
            "road_exposure_firehouse_m": (
                round(fh_dist, 1) if fh_dist is not None else None
            ),
            "road_exposure_index": round(index, 4),
        }
