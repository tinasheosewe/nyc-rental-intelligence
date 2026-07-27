"""
ShelterScorer — scores listings by street-homelessness exposure: observed
street conditions (311 Encampment complaints) blended with proximity to
homeless-services facilities AND NYCHA public housing projects.

Uses pre-downloaded datasets:
- NYC Facilities Database filtered for ``NON-RESIDENTIAL HOUSING AND
  HOMELESS SERVICES`` (shelters, drop-in centers, supportive housing).
- NYCHA BBL Extract — all 2,964 public housing buildings across 219
  developments ("the projects").
- 311 Service Requests (``ds_street_qol``) — 'Encampment' complaints,
  the *direct* observation of street conditions (facilities are the proxy).
- NYPD shootings (``ds_shootings``) — 2-year incident history used to
  weight each NYCHA development by its own violence record.

Scoring model — blended exposure, baseline-normalized:

1.  OBSERVED street condition (~half the total weight):
    Encampment complaints within 200 m, each weighted by a Gaussian
    distance kernel (sigma 100 m) x recency decay (half-life 180 d),
    accumulated PER LOCATION (~11 m cell) and capped at 6.0 per location
    so one serial complainant can't paint the block. The capped sum is
    divided by the cap so a fully-saturated persistent encampment counts
    like ~1 facility at the door.

2.  FACILITY proxy (~the other half):
    a. Homeless-services facilities within 800 m, Gaussian-kernel
       weighted (sigma 400 m), each scaled by its ``factype`` street
       impact: drop-in / intake / outreach x1.5 (highest churn),
       congregate shelter x1.2, supported SRO / supportive housing x0.4
       (housed neighbors, minimal street impact), HomeBase prevention
       offices x0.2 (administrative), anything else x1.0.
    b. NYCHA developments within 800 m (grouped so one complex counts
       once), kernel-weighted by nearest building, each scaled by the
       development's OWN 2-yr shooting count within 150 m of its
       buildings: 0 shootings x0.4 (41% of developments — they should
       not penalize like the p95), 1-2 x1.0, 3+ x1.6. If ds_shootings
       is missing or still has the swapped-lat/lon bug (MIN(latitude)
       < 0 → unrepaired), weights stay flat at x1.0.

3.  ``shelter_exposure_total = 0.5 * facility_term + 0.5 * observed``
    is scored against the frozen citywide baseline (*inverted*: less
    exposure → higher score; exactly 0 pins to 100). Falls back to
    batch-median normalization until the next ``build_baseline.py`` run.

Degradation: re-downloads may be in flight — ds_street_qol / factype /
ds_shootings are each probed once per ``score()`` call and the affected
term degrades (observed term 0, flat subtype weights, flat violence
weights). Blocks computed in a degraded state are NOT cached, so full
stats populate as soon as the data lands.

Component columns stored: ``shelter_count``, ``shelter_nearest_m``,
``shelter_nearest_name``, ``shelter_weighted_total``,
``project_count``, ``project_nearest_m``, ``project_nearest_name``,
plus NEW ``shelter_encampment_count``, ``shelter_encampment_weighted``,
``shelter_exposure_total``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from math import exp
from typing import Optional

from haversine import haversine, Unit

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores, decay_weight
from apthunt.scoring.utils import dedupe_by_geohash, median_inverse_scores

_RADIUS_M = 800

# Cache version: v3 — subtype weights + NYCHA violence weights + observed
# encampment term (raw-metric semantics changed; rebaseline follows).
_CACHE_KEY = "shelter_v3"

# ── Observed street condition (311 'Encampment') ─────────────────────
ENCAMP_RADIUS_M = 200
ENCAMP_SIGMA_M = ENCAMP_RADIUS_M / 2.0          # 100 m
# Max decayed+kernel weight one ~11 m location may contribute — a single
# serial complainant tops out at ~6 effective complaints/yr (same damping
# as NoiseScorer). Also the facility-equivalence divisor: a location at
# the cap (persistent, repeatedly-reported encampment) ≈ 1 facility.
ENCAMP_LOCATION_CAP = 6.0

# Blend: the direct observation carries ~half the total signal; the
# facility/NYCHA proxy carries the other half.
OBSERVED_BLEND = 0.5
FACILITY_BLEND = 0.5

# ── Facility subtype street-impact weights (factype substring match,
#    checked in precedence order — first hit wins) ────────────────────
_FACTYPE_WEIGHTS = (
    (("DROP-IN", "INTAKE", "OUTREACH"), 1.5),   # highest street churn
    (("SHELTER",), 1.2),                        # congregate shelter
    (("SUPPORTED", "SUPPORTIVE", "SRO"), 0.4),  # housed neighbors
    (("HOMEBASE",), 0.2),                       # prevention offices
)

# ── NYCHA violence weighting ─────────────────────────────────────────
SHOOTING_NEAR_DEV_M = 150.0
SHOOTING_LOOKBACK_DAYS = 730


def _factype_weight(factype) -> float:
    """Street-impact multiplier for a facility's factype string.

    FacDB factype values may be comma-joined multi-program strings
    ("SUPPORTED HOUSING-OMH, SUPPORTED SRO-OMH, ..."), so match by
    substring; precedence order resolves mixes ("SHELTER INTAKE" → 1.5).
    Unrecognized types keep the historical flat weight 1.0.
    """
    ft = (factype or "").upper()
    for keys, w in _FACTYPE_WEIGHTS:
        for k in keys:
            if k in ft:
                return w
    return 1.0


def _shooting_multiplier(n_incidents: int) -> float:
    """Scale a NYCHA development's proximity weight by its own violence.

    41% of developments had ZERO shootings in 2 years — they should not
    penalize like the p95 development.
    """
    if n_incidents <= 0:
        return 0.4
    if n_incidents <= 2:
        return 1.0
    return 1.6


class ShelterScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "shelter"

    # Citywide baseline: sampled by scripts/build_baseline.py.
    # Blended raw metric (observed encampments + weighted facilities) —
    # semantics changed this wave; a rebaseline follows.
    baseline_component = "shelter_exposure_total"
    baseline_reverse = True          # less exposure = better
    baseline_zero_perfect = True     # nothing within radius → 100

    def columns(self) -> dict[str, str]:
        return {
            "shelter_count": "INTEGER",
            "shelter_nearest_m": "INTEGER",
            "shelter_nearest_name": "TEXT",
            "shelter_weighted_total": "REAL",
            "project_count": "INTEGER",
            "project_nearest_m": "INTEGER",
            "project_nearest_name": "TEXT",
            "shelter_encampment_count": "INTEGER",
            "shelter_encampment_weighted": "REAL",
            "shelter_exposure_total": "REAL",
        }

    # ------------------------------------------------------------------
    # Availability probes — once per score() call. Re-downloads may be
    # in flight in another process; each dependency degrades on its own.
    # ------------------------------------------------------------------

    def _probe_encampment(self) -> bool:
        """ds_street_qol present and queryable?"""
        try:
            self._store.query("street_qol", select="complaint_type", limit=1)
            return True
        except sqlite3.OperationalError:
            return False

    def _probe_factype(self) -> bool:
        """ds_shelters carries the factype column?"""
        try:
            self._store.query("shelters", select="factype", limit=1)
            return True
        except sqlite3.OperationalError:
            return False

    def _probe_shootings(self) -> bool:
        """ds_shootings present AND lat/lon repaired?

        The upstream feed ships some rows with swapped lat/lon; the
        ``fix_swapped_latlon`` post-process repairs them at download
        time. An unrepaired table shows MIN(latitude) < 0 (longitudes
        in the latitude column) — in that state distance math is
        garbage, so violence weights stay flat.
        """
        try:
            row = self._store.query(
                "shootings", select="MIN(latitude) AS min_lat", limit=1
            )
            min_lat = row[0].get("min_lat") if row else None
            return min_lat is not None and float(min_lat) > 0
        except (sqlite3.OperationalError, TypeError, ValueError):
            return False

    # ------------------------------------------------------------------
    # Main scoring entry point
    # ------------------------------------------------------------------

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("shelters", quiet=True)
        self._store.ensure_downloaded("projects", quiet=True)
        # Shared datasets owned by other scorers — ensure but never fail
        # the whole scorer on a download hiccup (probes handle absence).
        for extra in ("street_qol", "shootings"):
            try:
                self._store.ensure_downloaded(extra, quiet=True)
            except Exception:
                pass

        enc_ok = self._probe_encampment()
        factype_ok = self._probe_factype()
        shoot_ok = self._probe_shootings()
        # Only cache fully-informed stats: blocks computed while a
        # dependency is missing/unrepaired would otherwise pin degraded
        # numbers for the whole cache TTL after the re-download lands.
        cache_ok = enc_ok and factype_ok and shoot_ok

        today_ord = datetime.now().toordinal()
        shoot_cutoff = (
            datetime.now() - timedelta(days=SHOOTING_LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")

        gh_map = dedupe_by_geohash(listings)

        block_stats: dict[str, dict] = {}
        for gh, (lat, lon) in gh_map.items():
            if cache_ok:
                cached = self._cache.get(gh, _CACHE_KEY)
                if cached is not None:
                    block_stats[gh] = cached
                    continue
            stats = self._block_stats(
                lat, lon, today_ord,
                enc_ok=enc_ok, factype_ok=factype_ok, shoot_ok=shoot_ok,
                shoot_cutoff=shoot_cutoff,
            )
            block_stats[gh] = stats
            if cache_ok:
                self._cache.put(gh, _CACHE_KEY, stats)

        # ── Score blended exposure against the citywide baseline
        #    (inverted: less exposure = better); fall back to batch-
        #    median until the post-wave rebaseline ──
        per_listing = [block_stats[lst["geohash"]]["shelter_exposure_total"]
                       for lst in listings]
        scores = baseline_scores(
            conn, self.name, per_listing,
            reverse=True, zero_is_perfect=True,
        )
        if scores is None:
            baseline = [v["shelter_exposure_total"] for v in block_stats.values()]
            scores = median_inverse_scores(per_listing, baseline=baseline)

        results: list[ScorerResult] = []
        for lst, sc in zip(listings, scores):
            stats = block_stats[lst["geohash"]]

            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=round(sc, 1),
                    components={
                        "shelter_count": stats["shelter_count"],
                        "shelter_nearest_m": stats["shelter_nearest_m"],
                        "shelter_nearest_name": stats["shelter_nearest_name"],
                        "shelter_weighted_total": stats["shelter_weighted_total"],
                        "project_count": stats["project_count"],
                        "project_nearest_m": stats["project_nearest_m"],
                        "project_nearest_name": stats["project_nearest_name"],
                        "shelter_encampment_count": stats["shelter_encampment_count"],
                        "shelter_encampment_weighted": stats[
                            "shelter_encampment_weighted"
                        ],
                        "shelter_exposure_total": stats["shelter_exposure_total"],
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # Per-block computation
    # ------------------------------------------------------------------

    def _block_stats(
        self,
        lat: float,
        lon: float,
        today_ord: int,
        *,
        enc_ok: bool,
        factype_ok: bool,
        shoot_ok: bool,
        shoot_cutoff: str,
    ) -> dict:
        # Gaussian distance kernel: 1.0 at the door, ~0.02 at the radius edge.
        sigma = _RADIUS_M / 2.0

        # ── Shelters (subtype-weighted) ──────────────────
        select = "facname,factype,latitude,longitude" if factype_ok \
            else "facname,latitude,longitude"
        shelter_rows = self._store.query_circle(
            "shelters", lat=lat, lon=lon, radius_m=_RADIUS_M, select=select,
        )

        s_count = 0
        s_weighted = 0.0
        s_nearest_m = 9999
        s_nearest_name = ""

        for r in shelter_rows:
            rlat = float(r.get("latitude") or 0)
            rlon = float(r.get("longitude") or 0)
            if rlat == 0 or rlon == 0:
                continue
            dist = float(r["_dist_m"])
            if dist > _RADIUS_M:
                continue
            s_count += 1
            subtype_w = _factype_weight(r.get("factype")) if factype_ok else 1.0
            s_weighted += subtype_w * exp(-((dist / sigma) ** 2))
            if dist < s_nearest_m:
                s_nearest_m = int(dist)
                s_nearest_name = r.get("facname") or ""

        # ── NYCHA projects (violence-weighted) ───────────
        # Group by development name so that one housing complex with many
        # buildings counts as ONE facility, not N.
        project_rows = self._store.query_circle(
            "projects",
            lat=lat, lon=lon, radius_m=_RADIUS_M,
            select="development,latitude,longitude",
        )

        # dev_name → nearest distance (m); dev_name → in-radius buildings
        dev_nearest: dict[str, float] = {}
        dev_buildings: dict[str, list] = {}

        for r in project_rows:
            rlat = float(r.get("latitude") or 0)
            rlon = float(r.get("longitude") or 0)
            if rlat == 0 or rlon == 0:
                continue
            dist = float(r["_dist_m"])
            if dist > _RADIUS_M:
                continue
            dev = r.get("development") or "unknown"
            if dev not in dev_nearest or dist < dev_nearest[dev]:
                dev_nearest[dev] = dist
            dev_buildings.setdefault(dev, []).append((rlat, rlon))

        # None → flat x1.0 weights (shootings unavailable/unrepaired);
        # a dict has a count for EVERY in-radius development, so x0.4
        # for zero-shooting devs only ever comes from real data.
        dev_violence = None
        if shoot_ok and dev_buildings:
            dev_violence = self._dev_shooting_counts(
                lat, lon, dev_buildings, shoot_cutoff
            )

        p_count = len(dev_nearest)
        p_weighted = 0.0
        for dev, d in dev_nearest.items():
            mult = (
                _shooting_multiplier(dev_violence.get(dev, 0))
                if dev_violence is not None else 1.0
            )
            p_weighted += mult * exp(-((d / sigma) ** 2))
        p_nearest_m = 9999
        p_nearest_name = ""
        if dev_nearest:
            nearest_dev = min(dev_nearest, key=dev_nearest.get)
            p_nearest_m = int(dev_nearest[nearest_dev])
            p_nearest_name = nearest_dev

        # ── Observed street condition (311 'Encampment') ─
        enc_count, enc_weighted = (
            self._encampment_burden(lat, lon, today_ord) if enc_ok else (0, 0.0)
        )

        # ── Blended exposure ─────────────────────────────
        facility_term = s_weighted + p_weighted
        observed_term = enc_weighted / ENCAMP_LOCATION_CAP
        exposure = (
            FACILITY_BLEND * facility_term + OBSERVED_BLEND * observed_term
        )

        return {
            "shelter_count": s_count,
            "shelter_nearest_m": s_nearest_m if s_count > 0 else 9999,
            "shelter_nearest_name": s_nearest_name,
            "shelter_weighted_total": round(facility_term, 3),
            "project_count": p_count,
            "project_nearest_m": p_nearest_m if p_count > 0 else 9999,
            "project_nearest_name": p_nearest_name,
            "shelter_encampment_count": enc_count,
            "shelter_encampment_weighted": round(enc_weighted, 3),
            "shelter_exposure_total": round(exposure, 3),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _encampment_burden(
        self, lat: float, lon: float, today_ord: int
    ) -> "tuple[int, float]":
        """Kernel- and recency-weighted, per-location-capped Encampment
        complaint burden within ENCAMP_RADIUS_M."""
        rows = self._store.query_circle(
            "street_qol",
            lat=lat, lon=lon, radius_m=ENCAMP_RADIUS_M,
            select="complaint_type,created_date,latitude,longitude",
        )
        count = 0
        per_loc: dict = {}
        for r in rows:
            if (r.get("complaint_type") or "") != "Encampment":
                continue
            count += 1
            dt = (r.get("created_date") or "")[:10]
            dist = float(r.get("_dist_m") or 0.0)
            kernel = exp(-((dist / ENCAMP_SIGMA_M) ** 2))
            try:
                loc = (round(float(r.get("latitude")), 4),
                       round(float(r.get("longitude")), 4))
            except (TypeError, ValueError):
                loc = ("?", count)  # unknown location: never capped together
            per_loc[loc] = per_loc.get(loc, 0.0) + kernel * decay_weight(
                dt, today_ord
            )
        weighted = sum(min(w, ENCAMP_LOCATION_CAP) for w in per_loc.values())
        return count, weighted

    def _dev_shooting_counts(
        self,
        lat: float,
        lon: float,
        dev_buildings: "dict[str, list]",
        shoot_cutoff: str,
    ) -> "Optional[dict[str, int]]":
        """2-yr shooting incidents within SHOOTING_NEAR_DEV_M of each
        development's (in-radius) buildings, deduped by incident.

        One query for the whole block (radius covers every building plus
        the 150 m ring), then per-building Haversine — ds_shootings is
        small (~1-2k rows citywide), so this is cheap.

        Returns None if the table vanished mid-call (re-download swap) —
        the caller falls back to flat weights, NOT to "zero shootings".
        """
        try:
            rows = self._store.query_circle(
                "shootings",
                lat=lat, lon=lon,
                radius_m=_RADIUS_M + SHOOTING_NEAR_DEV_M,
                select="incident_key,occur_date,latitude,longitude",
            )
        except sqlite3.OperationalError:
            return None

        incidents = []
        for r in rows:
            if (r.get("occur_date") or "") < shoot_cutoff:
                continue
            try:
                slat = float(r["latitude"])
                slon = float(r["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            # Multi-victim incidents repeat incident_key — count once.
            key = r.get("incident_key") or (slat, slon, r.get("occur_date"))
            incidents.append((key, slat, slon))

        counts: dict = {}
        for dev, buildings in dev_buildings.items():
            seen: set = set()
            for key, slat, slon in incidents:
                if key in seen:
                    continue
                for blat, blon in buildings:
                    d = haversine((blat, blon), (slat, slon), unit=Unit.METERS)
                    if d <= SHOOTING_NEAR_DEV_M:
                        seen.add(key)
                        break
            counts[dev] = len(seen)
        return counts
