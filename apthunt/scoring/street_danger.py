"""
StreetDangerScorer — scores listings by pedestrian / cyclist street danger.

Data source: ``ds_street_collisions`` (NYPD Motor Vehicle Collisions,
pre-filtered at download time to crashes that injured or killed a
pedestrian or cyclist within the last 12 months).  Road-class context
comes from ``ds_roads`` (OSM road points with ``road_class``/``name``).

Method:
    All qualifying crashes within 300 m of the listing's block are
    combined into a single weighted exposure metric.  Each crash
    contributes a base weight::

        severity = (ped_injured + cyc_injured) + 10 * (ped_killed + cyc_killed)
        w        = severity * exp(-(d / 150)**2) * decay_weight(crash_date)

    i.e. a Gaussian distance kernel (sigma 150 m — a crash at the corner
    matters far more than one 300 m away) times the standard 6-month
    half-life recency decay.  Deaths count 10x injuries.

    Frontage-vs-crossing decomposition (v3):
        A raw kernel sum smears arterial carnage onto quiet side streets:
        71% of ped/cyc-injury crashes lie on major roads, yet 62% of
        listings sit >40 m from any major road.  A listing on a leafy
        block a half-block off Queens Blvd absorbed Queens Blvd's crash
        history at full weight even though its residents only *cross*
        the arterial occasionally.  So each crash's contribution is
        split by where it happened relative to the listing's own street:

        * crash within 30 m of a major-road point (motorway..primary):
            - listing's own frontage IS that road (a major-road point of
              the same road within 40 m of the listing) → x1.0 — you
              live on the arterial, its danger is your doorstep;
            - listing's frontage is NOT that road (>40 m)  → x0.45 —
              you cross that arterial sometimes, you don't live on it.
          Road identity matches by OSM ``name`` when present, falling
          back to ``road_class`` for unnamed ways.
          → summed into ``street_danger_arterial_weighted``
        * crash NOT on a major road (side-street crash near home)
              → x1.3 — unavoidable exposure on the streets you walk
              every single day.
          → summed into ``street_danger_frontage_weighted``

        When ``ds_roads`` is unavailable (re-download in flight) the
        split degrades gracefully: every crash counts x1.0 into the
        frontage bucket (neutral, ≈ v2 behavior) and the arterial
        bucket is 0.

    The decomposed sum alone is still population-density confounded:
    crowded neighborhoods generate more crash *volume* at identical
    per-person risk.  So the scored metric is a per-household rate::

        street_danger_rate_per_khh =
            1000 * (frontage_weighted + arterial_weighted)
                 / kernel_weighted_units(store, lat, lon, 300)

    i.e. decomposed weighted casualties per 1000 households, using the
    same Gaussian kernel and radius on the denominator (PLUTO
    residential units).  Caveat: street injuries track foot traffic,
    and foot traffic tracks more than residents (transit, retail,
    nightlife), so per-household is an imperfect exposure proxy here —
    but it is far better than raw counts, which just measure
    crowdedness.  NB the raw-metric definition changed in v3 (the
    decomposition multipliers); a citywide rebaseline follows this wave.

    The rate is scored against the frozen citywide baseline (lower =
    better; zero is perfect).  Until a baseline exists, an absolute
    exponential fallback is used: ``100 * exp(-rate / 8)`` — no nearby
    crashes scores 100 and the score approaches 0 for chronic
    high-injury corridors.

    School-hours context (explanatory only, no re-weight): the share of
    nearby qualifying crashes whose stored ``crash_time`` falls in the
    school-commute windows (07-08h and 14-16h) is emitted as
    ``street_danger_school_hours_share`` so family-context explanations
    can say "a third of the injuries here happen at drop-off/pick-up".
    ``None`` when the crash_time column hasn't landed yet or no nearby
    crash has a parseable time.

Output columns:
    street_danger_injuries           INTEGER — ped + cyclist injuries within 300 m (12 mo)
    street_danger_deaths             INTEGER — ped + cyclist deaths within 300 m (12 mo)
    street_danger_weighted           REAL    — severity x distance-kernel x recency sum (undecomposed)
    street_danger_rate_per_khh       REAL    — decomposed weighted sum per 1000 kernel-weighted households
    street_danger_frontage_weighted  REAL    — side-street (non-major-road) crashes, x1.3
    street_danger_arterial_weighted  REAL    — major-road crashes, x0.45 off-frontage / x1.0 on-frontage
    street_danger_school_hours_share REAL    — share of nearby crashes at 07-08h / 14-16h (may be NULL)
"""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores, decay_weight
from apthunt.scoring.utils import dedupe_by_geohash, kernel_weighted_units


RADIUS_M = 300
KERNEL_SIGMA_M = 150.0
DEATH_WEIGHT = 10.0
# Absolute fallback scale: score = 100 * exp(-rate / FALLBACK_SCALE).
# rate ~= recency/distance-discounted casualties per 1000 households,
# so 0 -> 100, ~5.5 -> 50, ~18 -> 10.
FALLBACK_SCALE = 8.0

# ── Frontage-vs-crossing decomposition ───────────────────────────────
# Road classes that count as "major" for the decomposition (ds_roads).
MAJOR_ROAD_CLASSES = frozenset(
    {"motorway", "motorway_link", "trunk", "trunk_link", "primary"}
)
# A crash within this distance of a major-road point is "on" that road.
CRASH_ON_MAJOR_MAX_M = 30.0
# A major-road point within this distance of the listing means the
# listing's own frontage is that road (beyond it, you merely cross it).
FRONTAGE_MAX_M = 40.0
# Arterial crash, listing does NOT front that road: occasional-crossing
# exposure.
ARTERIAL_CROSSING_WEIGHT = 0.45
# Side-street crash near home: unavoidable daily exposure.
SIDE_STREET_WEIGHT = 1.3

# School-commute hours for the explanatory share (07-08h + 14-16h).
SCHOOL_HOURS = frozenset({7, 8, 14, 15, 16})

# v3: frontage/arterial decomposition + school-hours share (raw-metric
# semantics changed — old cached stats are not comparable).
_CACHE_KEY = "street_danger_v3"


class StreetDangerScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "street_danger"

    # Citywide baseline metric (sampled by scripts/build_baseline.py).
    # Per-household rate, not the raw weighted count — raw counts are
    # population-density confounded (see module docstring).
    baseline_component = "street_danger_rate_per_khh"
    baseline_reverse = True
    baseline_zero_perfect = True

    def columns(self) -> dict[str, str]:
        return {
            "street_danger_injuries": "INTEGER",
            "street_danger_deaths": "INTEGER",
            "street_danger_weighted": "REAL",
            "street_danger_rate_per_khh": "REAL",
            "street_danger_frontage_weighted": "REAL",
            "street_danger_arterial_weighted": "REAL",
            "street_danger_school_hours_share": "REAL",
        }

    # ------------------------------------------------------------------
    # Main scoring entry point
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("street_collisions", quiet=True)

        # The dataset may still be mid-download in another process — probe
        # once and degrade gracefully if the table isn't there yet.
        try:
            self._store.query("street_collisions", select="collision_id", limit=1)
        except sqlite3.OperationalError:
            return [
                ScorerResult(listing_id=lst["id"], score=None, components={})
                for lst in listings
            ]

        # Probe once per score() call for in-flight re-download deps:
        # crash_time is a NEW column and ds_roads may not have landed yet.
        try:
            self._store.query("street_collisions", select="crash_time", limit=1)
            has_crash_time = True
        except sqlite3.OperationalError:
            has_crash_time = False
        try:
            self._store.query("roads", select="road_class", limit=1)
            roads_available = True
        except sqlite3.OperationalError:
            roads_available = False

        # Deduplicate by geohash (one lookup per block)
        gh_map = dedupe_by_geohash(listings)

        today_ord = datetime.now().toordinal()

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            cached = self._cache.get(gh, _CACHE_KEY)
            if cached is not None:
                block_stats[gh] = cached
                continue
            stats = self._block_danger(
                lat, lon, today_ord,
                has_crash_time=has_crash_time,
                roads_available=roads_available,
            )
            block_stats[gh] = stats
            self._cache.put(gh, _CACHE_KEY, stats)

        # Score the per-household rate against the frozen citywide
        # baseline (lower = better; zero_is_perfect: no nearby ped/cyc
        # casualties -> 100).
        raw = [
            block_stats[lst["geohash"]]["street_danger_rate_per_khh"]
            for lst in listings
        ]
        scores = baseline_scores(
            conn, self.name, raw, reverse=True, zero_is_perfect=True,
        )
        if scores is None:
            # Absolute fallback until the first baseline build: exponential
            # decay of the weighted casualty burden (defined for a single
            # listing, no batch-relative artifacts).
            scores = [
                round(100.0 * math.exp(-float(v) / FALLBACK_SCALE), 1)
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
                        "street_danger_injuries": stats["street_danger_injuries"],
                        "street_danger_deaths": stats["street_danger_deaths"],
                        "street_danger_weighted": stats["street_danger_weighted"],
                        "street_danger_rate_per_khh": stats[
                            "street_danger_rate_per_khh"
                        ],
                        "street_danger_frontage_weighted": stats[
                            "street_danger_frontage_weighted"
                        ],
                        "street_danger_arterial_weighted": stats[
                            "street_danger_arterial_weighted"
                        ],
                        "street_danger_school_hours_share": stats[
                            "street_danger_school_hours_share"
                        ],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _major_road_points(self, lat: float, lon: float) -> list[tuple]:
        """Major-road points near the block, as (name, class, lat, lon,
        dist_from_block_m) tuples.

        One query covers both uses: frontage detection (points within
        FRONTAGE_MAX_M of the listing) and crash-on-arterial tests
        (points within CRASH_ON_MAJOR_MAX_M of any in-radius crash).
        """
        try:
            rows = self._store.query_circle(
                "roads",
                lat=lat,
                lon=lon,
                radius_m=RADIUS_M + CRASH_ON_MAJOR_MAX_M,
                select="road_class,name,lat,lon",
                lat_col="lat",
                lon_col="lon",
            )
        except Exception:
            return []
        points = []
        for r in rows:
            cls = r.get("road_class") or ""
            if cls not in MAJOR_ROAD_CLASSES:
                continue
            points.append(
                (
                    (r.get("name") or "").strip(),
                    cls,
                    float(r["lat"]),
                    float(r["lon"]),
                    float(r.get("_dist_m") or 0.0),
                )
            )
        return points

    def _block_danger(
        self,
        lat: float,
        lon: float,
        today_ord: int,
        *,
        has_crash_time: bool,
        roads_available: bool,
    ) -> dict:
        """Weighted ped/cyclist casualty burden within RADIUS_M metres,
        decomposed into frontage (side-street) vs arterial exposure."""
        select = (
            "crash_date,latitude,longitude,"
            "number_of_pedestrians_injured,number_of_pedestrians_killed,"
            "number_of_cyclist_injured,number_of_cyclist_killed"
        )
        if has_crash_time:
            select += ",crash_time"
        rows = self._store.query_circle(
            "street_collisions",
            lat=lat,
            lon=lon,
            radius_m=RADIUS_M,
            select=select,
        )

        # The listing's own frontage: identities of major roads whose
        # points lie within FRONTAGE_MAX_M of the block point.  Identity
        # is the OSM name when present; unnamed ways fall back to class.
        major_points = self._major_road_points(lat, lon) if roads_available else []
        frontage_names = {
            name for name, _cls, _la, _lo, d in major_points
            if name and d <= FRONTAGE_MAX_M
        }
        frontage_classes = {
            cls for name, cls, _la, _lo, d in major_points
            if not name and d <= FRONTAGE_MAX_M
        }

        injuries = 0
        deaths = 0
        weighted = 0.0
        frontage_weighted = 0.0
        arterial_weighted = 0.0
        timed_crashes = 0
        school_crashes = 0
        for r in rows:
            inj = _num(r.get("number_of_pedestrians_injured")) + _num(
                r.get("number_of_cyclist_injured")
            )
            kil = _num(r.get("number_of_pedestrians_killed")) + _num(
                r.get("number_of_cyclist_killed")
            )
            if inj <= 0 and kil <= 0:
                continue
            injuries += int(inj)
            deaths += int(kil)

            severity = inj + DEATH_WEIGHT * kil
            dist = float(r.get("_dist_m") or 0.0)
            kernel = math.exp(-((dist / KERNEL_SIGMA_M) ** 2))
            recency = decay_weight((r.get("crash_date") or "")[:10], today_ord)
            w = severity * kernel * recency
            weighted += w

            # Frontage-vs-crossing decomposition (see module docstring).
            if not roads_available:
                # No road context yet (re-download in flight): neutral
                # x1.0 into the frontage bucket ≈ v2 behavior.
                frontage_weighted += w
            else:
                on_road = _nearest_major_road(
                    major_points,
                    float(r["latitude"]),
                    float(r["longitude"]),
                )
                if on_road is None:
                    # Side-street crash near home: unavoidable exposure.
                    frontage_weighted += SIDE_STREET_WEIGHT * w
                else:
                    name, cls = on_road
                    fronts_it = (
                        name in frontage_names
                        if name
                        else cls in frontage_classes
                    )
                    mult = 1.0 if fronts_it else ARTERIAL_CROSSING_WEIGHT
                    arterial_weighted += mult * w

            # School-hours share (explanatory only — no re-weight).
            if has_crash_time:
                hour = _parse_hour(r.get("crash_time"))
                if hour is not None:
                    timed_crashes += 1
                    if hour in SCHOOL_HOURS:
                        school_crashes += 1

        # Density-corrected rate: decomposed weighted casualties per 1000
        # kernel-weighted households (same radius on both sides).
        # Imperfect — street injuries correlate with foot traffic, which
        # includes non-residents (transit, retail, nightlife) — but far
        # better than raw counts, which mostly measure crowdedness.
        units = kernel_weighted_units(self._store, lat, lon, RADIUS_M)
        rate = 1000.0 * (frontage_weighted + arterial_weighted) / units

        school_share = (
            round(school_crashes / timed_crashes, 4) if timed_crashes else None
        )

        return {
            "street_danger_injuries": injuries,
            "street_danger_deaths": deaths,
            "street_danger_weighted": round(weighted, 4),
            "street_danger_rate_per_khh": round(rate, 4),
            "street_danger_frontage_weighted": round(frontage_weighted, 4),
            "street_danger_arterial_weighted": round(arterial_weighted, 4),
            "street_danger_school_hours_share": school_share,
        }


def _nearest_major_road(
    major_points: list[tuple],
    lat: float,
    lon: float,
):
    """(name, class) of the nearest major-road point within
    CRASH_ON_MAJOR_MAX_M of (lat, lon), or None if the location is not
    on a major road.  Equirectangular meters (NYC: 1° lon ≈ 85 km)."""
    best = None
    best_d = CRASH_ON_MAJOR_MAX_M
    for name, cls, plat, plon, _d in major_points:
        dy = (plat - lat) * 111_320.0
        dx = (plon - lon) * 85_000.0
        d = math.hypot(dx, dy)
        if d <= best_d:
            best = (name, cls)
            best_d = d
    return best


def _parse_hour(raw) -> int | None:
    """Hour (0-23) from a stored NYPD crash_time like "14:30" / "7:05";
    None on junk."""
    try:
        hour = int(str(raw).strip().split(":", 1)[0])
    except (ValueError, AttributeError):
        return None
    return hour if 0 <= hour <= 23 else None


def _num(v) -> float:
    """Coerce a possibly-NULL/text numeric field to float (0.0 on junk)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
