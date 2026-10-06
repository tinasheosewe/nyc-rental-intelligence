"""
NoiseScorer — scores listings by 311 noise-complaint density.

Uses pre-downloaded 311 Service Requests (``ds_noise``) with Haversine
circle queries cached in BlockCache by geohash.

Filters to actual noise types only:
    Noise - Residential, Noise - Street/Sidewalk, Noise - Commercial,
    Noise - Vehicle, Noise - Park, and generic "Noise" (mostly
    construction; requires the re-downloaded dataset — degrades to the
    five specific types against an older DB).

Scoring:
    Count noise complaints within 150 m in the last 12 months, each
    weighted by recency (exponential decay, half-life 180 days) and by
    distance (Gaussian kernel, sigma = radius/2 = 75 m).  Generic "Noise"
    complaints with after-hours-construction descriptors are up-weighted
    x1.2 (sleep-relevant); other generic Noise counts x1.0.

    Kernel size is a measured decision, not a default: at 300 m/sigma-150
    North Williamsburg's nightlife strips (spaced every 2-3 blocks) put
    EVERY residential cell within ~1 sigma of a strip — a quiet side
    street (Fillmore Pl) carried 0.96x the raw rate of the Bedford &
    N 7th nightlife core, flattening whole neighborhoods to the strip
    value.  At 150 m/sigma-75 the same side street reads 0.49x the core:
    strips stay loud, the streets between them recover their gradient.

    Serial-complainant guard: weight accumulates per (location, type)
    and is capped PER TYPE.  Residential/Street complaints cap low (6.0
    — one aggrieved neighbor must not paint the block); Commercial caps
    high (20.0 — chronic venues are REAL signal: 34% of commercial noise
    complaints come from >50/yr locations and a flat cap crushed them);
    Vehicle/Park/generic sit between (10.0).

    Structural nightlife prior: kernel-weighted on-premises liquor-
    license density (``ds_liquor_licenses``, Spearman 0.68 vs commercial
    complaints) enters the rate numerator at ~0.3 weight — a predictive
    prior for blocks whose complaint record understates their nightlife;
    complaints stay dominant.

    Seasonality: noise complaints are strongly seasonal (the current
    rolling window peaks in WINTER — Dec ~1.42x the monthly mean, Jul
    ~0.74x — heating-season indoor complaints dominate), so the decayed
    complaint sum is divided by ``seasonal_factor`` — the current
    month's share of annual ds_noise volume — removing the
    month-of-scoring bias.  Applied at score time (not baked into the
    cache) so cached blocks stay month-independent.

    The scored metric is a per-household rate: the seasonally-adjusted
    weighted sum (plus the nightlife prior) divided by Gaussian-kernel-
    weighted PLUTO residential units in the same radius, x1000
    (per-1000-households).  The denominator is floored at
    RATE_UNITS_FLOOR (150): per-location caps are absolute while the
    denominator scales with density, so an unfloored sparse cell had
    effectively no serial-complainant protection — one fresh complaint
    at 25 kernel units cost 35 points, and a McCarren-edge cell scored
    9.6 on one-tenth the complaint mass of the Bedford core purely
    because few households live beside a park.  The rate is scored
    against the frozen citywide baseline (absolute); fall back to
    median-inverse until a baseline is built.  Fewer complaints per
    household -> higher score.

    Trend: compares recent-half (last 6 months) vs older-half
    to produce a trend ratio.  < 1.0 = improving, > 1.0 = worsening.
    If the dataset's freshest created_date lags more than
    TREND_LAG_MAX_DAYS behind today, the recent-half bucket is
    truncated by publication lag and any "improving" reading is an
    artifact — the trend is suppressed to stable for the whole batch.

NEGATIVE RESULT — do NOT re-add night-hour re-weighting:
    Up-weighting complaints filed during night hours (created_date
    timestamp between ~22:00 and 06:00) was tested and produced a rank
    correlation of 0.9966 against the unweighted metric — the block
    ranking is unchanged and the extra parsing is pure complexity.
    Noise complaints are already overwhelmingly nocturnal, so hour
    weighting adds no discrimination.  Leave it out.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime

from apthunt.data.block_cache import BlockCache
from apthunt.data.data_store import DataStore
from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.baseline import baseline_scores, decay_weight, seasonal_factor
from apthunt.scoring.utils import (
    TREND_MIDPOINT,
    compute_trend,
    dedupe_by_geohash,
    kernel_weighted_units,
    median_inverse_scores,
)


# ── Liquor-license classification (structural nightlife term) ────────
#
# SLA 'description' values keyed by nightlife relevance:
#   bar/club/cabaret/tavern-type  x1.5  (late, loud, chronic)
#   restaurant/eating-place       x0.6  (closes earlier, quieter)
#   off-premises retail/wholesale  EXCLUDED (grocery, liquor store,
#       drug store, wine store, wholesale, importer — bottles leave)
#   everything else on-premises   x1.0  (hotel, theatre, brewer, ...)
_NIGHTLIFE_EXCLUDE = (
    "grocery", "liquor store", "wine store", "drug store",
    "wholesale", "importer", "winemaker",
)
_NIGHTLIFE_BAR = ("bar", "club", "cabaret", "tavern")  # NB: "bar" in "cabaret" — same tier anyway
_NIGHTLIFE_RESTAURANT = ("restaurant", "eating", "catering", "food & beverage")


def _license_class_weight(description: str):
    """Nightlife weight for an SLA license description, or None to exclude."""
    d = (description or "").lower()
    if any(k in d for k in _NIGHTLIFE_EXCLUDE):
        return None  # off-premises: consumption happens elsewhere
    if any(k in d for k in _NIGHTLIFE_BAR):
        return 1.5
    if any(k in d for k in _NIGHTLIFE_RESTAURANT):
        return 0.6
    return 1.0


class NoiseScorer(Scorer):

    def __init__(self, store: DataStore, cache: BlockCache):
        self._store = store
        self._cache = cache

    @property
    def name(self) -> str:
        return "noise"

    # Citywide-baseline metric: per-1000-household complaint rate
    # (seasonally-adjusted decay- and distance-weighted sum, plus 0.3x
    # nightlife-license prior, / kernel-weighted PLUTO units).
    # Definition changed in the nuance wave — rebaseline follows.
    baseline_component = "noise_rate_per_khh"
    baseline_reverse = True
    baseline_zero_perfect = True

    # Publication-lag guard: if the dataset's newest created_date is more
    # than this many days old, the recent trend bucket is truncated and
    # the trend is suppressed (311 is fresh daily, so this should never
    # trip — kept for symmetry with lag-prone feeds like crime).
    TREND_LAG_MAX_DAYS = 30

    # Type-aware serial-complainant caps: max decayed+kernel weight one
    # location (~11m cell) may contribute, PER complaint type.
    # Residential/Street: one feuding neighbor tops out at ~6 effective
    # complaints/yr.  Commercial: chronic venues are genuine signal (34%
    # of commercial complaints come from >50/yr locations — the old flat
    # 6.0 cap crushed them), so the ceiling is 20.  Vehicle/Park and
    # generic construction "Noise" sit between.
    PER_LOCATION_CAPS = {
        "Noise - Residential": 6.0,
        "Noise - Street/Sidewalk": 6.0,
        "Noise - Commercial": 20.0,
        "Noise - Vehicle": 10.0,
        "Noise - Park": 10.0,
        "Noise": 10.0,  # cap unspecified in spec; middle tier (construction sites are real but bounded)
    }

    # After-hours construction (generic "Noise" descriptor) is the
    # sleep-relevant subset — up-weight it.
    AFTER_HOURS_DESCRIPTOR_WEIGHT = 1.2

    # Weight of the structural nightlife-license density in the rate
    # numerator: a predictive prior — complaints stay dominant.
    NIGHTLIFE_PRIOR_WEIGHT = 0.3

    # Rate-denominator floor (kernel-weighted units). Caps are absolute
    # while the denominator scales with density: unfloored, one fresh
    # complaint at 25 kernel units cost 35 score points and park-edge /
    # estate-area cells (McCarren edge, Fieldston) read "loud" on tiny
    # complaint mass. 150 units ≈ one small apartment building in the
    # 150 m disc; dense blocks (1000s of units) are unaffected.
    RATE_UNITS_FLOOR = 150.0

    def columns(self) -> dict[str, str]:
        return {
            "noise_complaint_count": "INTEGER",
            "noise_weighted": "REAL",
            "noise_nightlife_density": "REAL",
            "noise_rate_per_khh": "REAL",
            "noise_trend_ratio": "REAL",
            "noise_trend_direction": "TEXT",
        }

    # ── Availability probes (re-downloads in flight; degrade) ────────

    def _has_descriptor_column(self) -> bool:
        """True when ds_noise carries the descriptor column (new download)."""
        try:
            self._store.query("noise", select="descriptor", limit=1)
            return True
        except (sqlite3.Error, KeyError, ValueError):
            return False

    def _has_liquor_table(self) -> bool:
        """True when ds_liquor_licenses is present and geocoded."""
        try:
            self._store.query("liquor_licenses", select="latitude", limit=1)
            return True
        except (sqlite3.Error, KeyError, ValueError):
            return False

    def _nightlife_density(
        self, lat: float, lon: float, radius_m: float, sigma: float,
        today_iso: str,
    ) -> float:
        """Kernel-weighted on-premises liquor-license density around a point."""
        try:
            rows = self._store.query_circle(
                "liquor_licenses",
                lat=lat,
                lon=lon,
                radius_m=radius_m,
                select="description,expirationdate,latitude,longitude",
            )
        except (sqlite3.Error, KeyError, ValueError):
            return 0.0
        total = 0.0
        for r in rows:
            w = _license_class_weight(r.get("description") or "")
            if w is None:
                continue
            # Skip licenses that lapsed since the last download
            exp = (r.get("expirationdate") or "")[:10]
            if exp and exp < today_iso:
                continue
            dist_m = float(r.get("_dist_m") or 0.0)
            total += w * math.exp(-((dist_m / sigma) ** 2))
        return round(total, 3)

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        self._store.ensure_downloaded("noise", quiet=True)
        # Per-household rates divide by PLUTO residential units.
        self._store.ensure_downloaded("pluto", quiet=True)
        try:
            self._store.ensure_downloaded("liquor_licenses", quiet=True)
        except Exception:
            pass  # nightlife prior degrades to 0; noise scoring proceeds

        RADIUS_M = 150  # was 300 — see module docstring (strip-blur fix)
        # Complaint types to count (generic 'Noise' rows exist only in
        # the re-downloaded dataset; absent rows simply don't match).
        NOISE_TYPES = set(self.PER_LOCATION_CAPS)
        # Gaussian distance kernel: sigma = radius/2
        KERNEL_SIGMA_M = RADIUS_M / 2.0
        today_ord = datetime.now().toordinal()
        today_iso = date.today().isoformat()

        # Probe once per score() call: the noise re-download (descriptor
        # column + generic 'Noise' rows) and the liquor table may or may
        # not have landed yet — code must run against today's DB either way.
        has_descriptor = self._has_descriptor_column()
        has_liquor = self._has_liquor_table()

        # Cache version: v8 = 150 m kernel + floored denominator (v7 was
        # the 300 m nuance-wave kernel; seasonal factor stays OUTSIDE the
        # cache). Availability suffixes force a recompute for a block
        # once the in-flight re-downloads land.
        cache_key = "noise_v8" + ("d" if has_descriptor else "") + ("l" if has_liquor else "")

        noise_select = "complaint_type,created_date,latitude,longitude"
        if has_descriptor:
            noise_select += ",descriptor"

        # Publication-lag guard (computed once per batch): if the freshest
        # record lags too far behind today, the recent-half trend bucket is
        # incomplete and "improving" would be an artifact of missing data.
        suppress_trend = True
        try:
            row = self._store.query(
                "noise", select="MAX(created_date) AS max_dt", limit=1
            )
            max_dt = (row[0].get("max_dt") or "")[:10] if row else ""
            if max_dt:
                lag_days = today_ord - datetime.fromisoformat(max_dt).toordinal()
                suppress_trend = lag_days > self.TREND_LAG_MAX_DAYS
        except (ValueError, TypeError, IndexError, sqlite3.Error):
            pass  # unparseable/missing max date → keep trend suppressed

        # Geohash to lat/lon
        geohash_to_latlon = dedupe_by_geohash(listings)
        block_stats = {}
        for gh, (lat, lon) in geohash_to_latlon.items():
            cached = self._cache.get(gh, cache_key)
            if cached is not None:
                block_stats[gh] = cached
                continue
            # Query local DataStore (already filtered to relevant types + last 12 months)
            rows = self._store.query_circle(
                "noise",
                lat=lat,
                lon=lon,
                radius_m=RADIUS_M,
                select=noise_select,
            )
            noise = 0
            # Serial-complainant damping: 311 counts conflate breadth with
            # one aggrieved neighbor. Hundreds of complaints from a single
            # address (one feud, one venue) must not paint the whole block
            # "loud" — accumulate weight PER (LOCATION, TYPE) (~11m
            # rounding), then cap each location's contribution with the
            # TYPE-AWARE cap. Widespread noise (many locations) still
            # scores loud; one loud feud does not; a chronic venue does.
            per_loc: dict = {}
            recent_count, older_count = 0, 0
            for r in rows:
                ct = r.get("complaint_type") or ""
                if ct not in NOISE_TYPES:
                    continue
                noise += 1
                # Combined weight: recency decay × Gaussian distance kernel
                dt = (r.get("created_date") or "")[:10]
                dist_m = float(r.get("_dist_m") or 0.0)
                kernel = math.exp(-((dist_m / KERNEL_SIGMA_M) ** 2))
                w = kernel * decay_weight(dt, today_ord)
                # Generic 'Noise': after-hours construction descriptors
                # are the sleep-relevant subset — x1.2; others x1.0.
                if ct == "Noise" and has_descriptor:
                    desc = (r.get("descriptor") or "").lower()
                    if "after hours" in desc:
                        w *= self.AFTER_HOURS_DESCRIPTOR_WEIGHT
                try:
                    loc = (round(float(r.get("latitude")), 4),
                           round(float(r.get("longitude")), 4))
                except (TypeError, ValueError):
                    loc = ("?", noise)  # unknown location: never capped together
                per_loc[(loc, ct)] = per_loc.get((loc, ct), 0.0) + w
                # Bucket by date for trend
                if dt >= TREND_MIDPOINT:
                    recent_count += 1
                else:
                    older_count += 1
            weighted = sum(
                min(w, self.PER_LOCATION_CAPS[ct])
                for (_loc, ct), w in per_loc.items()
            )

            # Trend ratio: recent / older.  < 1.0 = improving
            trend_ratio, direction = compute_trend(recent_count, older_count)

            # Structural nightlife prior (0.0 when the table is absent)
            nightlife = (
                self._nightlife_density(lat, lon, RADIUS_M, KERNEL_SIGMA_M, today_iso)
                if has_liquor else 0.0
            )

            # Per-household denominator: same kernel on numerator and
            # denominator cancels the density confound (crowded ≠ noisy
            # per household). Cached raw so the seasonal factor — which
            # changes with the scoring month — is applied at score time.
            units = kernel_weighted_units(self._store, lat, lon, RADIUS_M)

            block_stats[gh] = {
                "noise_complaint_count": noise,
                "noise_total": noise,
                "noise_weighted": round(weighted, 3),
                "noise_nightlife_density": nightlife,
                "noise_trend_ratio": trend_ratio,
                "noise_trend_direction": direction,
                "_kernel_units": round(units, 3),
            }
            self._cache.put(gh, cache_key, block_stats[gh])

        # Seasonal normalization: complaints scored in July vs January
        # differ systematically; divide the decayed sum by the current
        # month's share of annual volume (mean-1.0 normalized). Applied
        # here — NOT baked into the cache — so cached blocks stay valid
        # across months. (See module docstring for the night-hour
        # re-weighting negative result; do not re-add it here.)
        season = seasonal_factor(conn, "ds_noise", "created_date")

        for stats in block_stats.values():
            adj = stats["noise_weighted"] / season
            numer = adj + self.NIGHTLIFE_PRIOR_WEIGHT * stats["noise_nightlife_density"]
            units = max(stats.get("_kernel_units") or 0.0, self.RATE_UNITS_FLOOR)
            stats["noise_rate_per_khh"] = round(1000.0 * numer / units, 3)

        per_listing = [block_stats[lst["geohash"]]["noise_rate_per_khh"] for lst in listings]
        # Absolute scoring against the frozen citywide baseline
        scores = baseline_scores(
            conn, self.name, per_listing, reverse=True, zero_is_perfect=True
        )
        if scores is None:
            # Fallback until first baseline build: batch-relative median-inverse
            baseline = [v["noise_rate_per_khh"] for v in block_stats.values()]
            scores = median_inverse_scores(per_listing, baseline=baseline)

        # Score: 50 = median, 100 = 0 complaints, 0 = 2× median or worse
        results = []
        for lst, score in zip(listings, scores):
            gh = lst["geohash"]
            stats = block_stats[gh]
            # Lag suppression applied at output time (not baked into the
            # cache) so trends reappear as soon as the feed catches up.
            if suppress_trend:
                trend_ratio, trend_direction = 1.0, "stable"
            else:
                trend_ratio = stats["noise_trend_ratio"]
                trend_direction = stats["noise_trend_direction"]
            results.append(
                ScorerResult(
                    listing_id=lst["id"],
                    score=score,
                    components={
                        "noise_complaint_count": stats["noise_complaint_count"],
                        "noise_weighted": stats["noise_weighted"],
                        "noise_nightlife_density": stats["noise_nightlife_density"],
                        "noise_rate_per_khh": stats["noise_rate_per_khh"],
                        "noise_trend_ratio": trend_ratio,
                        "noise_trend_direction": trend_direction,
                    },
                )
            )
        return results
