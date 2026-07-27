"""
DealScorer — multi-signal value score.

Combines up to five signals when data is available:
  1. Price vs neighborhood median  (weight 0.40)
  2. $/sqft vs neighborhood median (weight 0.30)
  3. Absolute sqft vs neighborhood median (weight 0.30)
  4. Tenant tenure bonus/penalty   (weight 0.10, applied as multiplier)
  5. Landlord-leverage: a FRESH price cut (≤21 days) adds
     +0.25 × cut_fraction to the deviation — recent softness means the
     achievable price sits below the current ask.

When sqft is unavailable the score falls back to price-only (weight 1.0).
Comp sets are grouped by (neighborhood, bed count) — no citywide fallback.
Scores are baselined citywide (fallback: batch z-scores, 50 = mean,
±25 per stdev, clamped 0–100).

Leverage signals (parsed from listings.raw_json — verified 100% of
active listings carry price_delta):
    deal_price_cut_pct     — cut as a FRACTION of the pre-cut ask
                             (0.04 = 4% cut; same units as deal_deviation,
                             emitted only when price_delta is negative)
    deal_months_free       — concession months from raw_json months_free;
                             already netted into net_effective_price, but
                             emitted visibly for the flag/UI layer
    deal_cut_recency_days  — days since price_changed_at, emitted for cuts

Stabilized unicorn (flag layer surfaces it; no score change):
    deal_stabilized_below_median = 1 when the unit is rent stabilized AND
    deal_deviation > 0.05 — the discount compounds at every renewal.

Tenure estimation:
    Derived from the listing's price_history "Listed" events.
    Rapid relists within 90 days are clustered into a single listing
    attempt (prevents "5 relists in 3 months = 5 short leases").
    Gap between clusters ≈ vacancy + lease term.  Estimated tenure
    = gap - 30 days assumed vacancy.
"""

from __future__ import annotations

import json
import statistics
import sqlite3
from datetime import date, datetime
from typing import Optional

from apthunt.scoring.base import Scorer, ScorerResult

MIN_COMP_SET = 3

# Signal weights (must sum to 1.0 for full-data case)
W_PRICE = 0.40
W_PRICE_PER_SQFT = 0.30
W_SQFT = 0.30
W_TENURE = 0.10  # applied by scaling down other weights 10%

# Tenure estimation constants
_RELIST_CLUSTER_DAYS = 90   # relists within this window = same attempt
_ASSUMED_VACANCY_DAYS = 30  # subtracted from gap to estimate net tenure
_BENCHMARK_MONTHS = 12      # "neutral" tenure (1-year lease)
_TENURE_SCALE = 24          # normalisation denominator for deviation

# Leverage-signal constants
_FRESH_CUT_DAYS = 21        # a cut this recent signals landlord softness
_FRESH_CUT_WEIGHT = 0.25    # deviation boost = weight × cut fraction
_MAX_CUT_FRAC = 0.5         # clamp against pathological raw_json deltas

# Stabilized-unicorn threshold: stabilized AND ≥5% below comp deviation
_STABILIZED_DEV_THRESHOLD = 0.05


def _true_cost(listing: dict) -> int | None:
    """Net effective price if available, otherwise ask price."""
    net = listing.get("net_effective_price")
    if net and net > 0:
        return net
    return listing.get("price")


class DealScorer(Scorer):

    @property
    def name(self) -> str:
        return "deal"

    # Baseline metric: combined value deviation vs comps (positive =
    # better value). Baselined against the active-listing distribution so
    # a single pasted listing scores absolutely (the old batch z-score
    # returned a meaningless 50.0 for a batch of one).
    baseline_component = "deal_deviation"
    baseline_reverse = False
    baseline_zero_perfect = False

    def columns(self) -> dict[str, str]:
        return {
            "comp_median": "INTEGER",
            "comp_set_size": "INTEGER",
            "comp_scope": "TEXT",
            "comp_sqft_median": "INTEGER",
            "price_per_sqft": "REAL",
            "tenure_median_months": "REAL",
            "tenure_cycle_count": "INTEGER",
            "deal_deviation": "REAL",
            "deal_price_cut_pct": "REAL",
            "deal_months_free": "REAL",
            "deal_cut_recency_days": "INTEGER",
            "deal_stabilized_below_median": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        # Build comp sets from all active listings
        price_comps = self._build_price_comp_sets(conn)
        sqft_comps = self._build_sqft_comp_sets(conn)
        ppsqft_comps = self._build_ppsqft_comp_sets(conn)

        # Pass 1: compute weighted deviation + metadata per listing
        intermediate: list[tuple[str, float, dict]] = []
        for lst in listings:
            cost = _true_cost(lst)
            if cost is None:
                continue

            key = (lst["neighborhood"], lst["beds"])
            prices = price_comps.get(key, [])

            # Need at least MIN_COMP_SET price comps in neighborhood
            if len(prices) < MIN_COMP_SET:
                continue

            price_median = statistics.median(prices)
            set_size = len(prices)

            if price_median == 0:
                continue

            # Signal 1: price vs median (positive = cheaper = better)
            price_dev = (price_median - cost) / price_median

            # Check if we can use sqft signals
            listing_sqft = lst.get("sqft")
            sqft_values = sqft_comps.get(key, [])
            ppsqft_values = ppsqft_comps.get(key, [])
            has_sqft = (
                listing_sqft
                and listing_sqft > 0
                and len(sqft_values) >= MIN_COMP_SET
                and len(ppsqft_values) >= MIN_COMP_SET
            )

            # Tenure estimation from price history
            tenure_months, tenure_cycles = self._estimate_tenure(lst)

            meta: dict = {
                "comp_median": int(price_median),
                "comp_set_size": set_size,
                "comp_scope": "neighborhood",
                "comp_sqft_median": None,
                "price_per_sqft": None,
                "tenure_median_months": tenure_months,
                "tenure_cycle_count": tenure_cycles,
            }

            if has_sqft:
                sqft_median = statistics.median(sqft_values)
                ppsqft_median = statistics.median(ppsqft_values)
                listing_ppsqft = cost / listing_sqft

                meta["comp_sqft_median"] = int(sqft_median) if sqft_median else None
                meta["price_per_sqft"] = round(listing_ppsqft, 2)

                # Signal 2: $/sqft vs median (positive = cheaper per sqft = better)
                ppsqft_dev = (ppsqft_median - listing_ppsqft) / ppsqft_median if ppsqft_median else 0.0

                # Signal 3: absolute sqft vs median (positive = bigger = better)
                sqft_dev = (listing_sqft - sqft_median) / sqft_median if sqft_median else 0.0

                combined = (
                    W_PRICE * price_dev
                    + W_PRICE_PER_SQFT * ppsqft_dev
                    + W_SQFT * sqft_dev
                )
            else:
                # Graceful degradation: price-only
                combined = price_dev

            # Signal 4: tenure bonus/penalty (when available)
            if tenure_months is not None:
                tenure_dev = (tenure_months - _BENCHMARK_MONTHS) / _TENURE_SCALE
                tenure_dev = max(-0.5, min(1.0, tenure_dev))
                # Scale existing signals down by (1 - W_TENURE), add tenure
                combined = combined * (1.0 - W_TENURE) + W_TENURE * tenure_dev

            # Signal 5: landlord-leverage from raw_json. A fresh cut
            # (≤ _FRESH_CUT_DAYS) means the achievable price sits below
            # the current ask — fold +0.25 × cut fraction into the
            # deviation. months_free already nets into net_effective,
            # so it is emitted visibly but NOT folded again.
            cut_frac, months_free, cut_recency = self._leverage_signals(lst)
            meta["deal_price_cut_pct"] = (
                round(cut_frac, 4) if cut_frac is not None else None
            )
            meta["deal_months_free"] = months_free
            meta["deal_cut_recency_days"] = cut_recency
            if (
                cut_frac is not None
                and cut_recency is not None
                and cut_recency <= _FRESH_CUT_DAYS
            ):
                combined += _FRESH_CUT_WEIGHT * cut_frac

            meta["deal_deviation"] = round(combined, 4)

            # Stabilized unicorn: rent stabilized AND meaningfully below
            # the comp median — the discount compounds at every renewal.
            # Emit-only (the flag layer surfaces it); no score change.
            # rent_stabilized may be absent mid-re-download → None (unknown).
            rs = lst.get("rent_stabilized")
            if rs is None:
                meta["deal_stabilized_below_median"] = None
            else:
                meta["deal_stabilized_below_median"] = int(
                    bool(rs) and combined > _STABILIZED_DEV_THRESHOLD
                )

            intermediate.append((lst["id"], combined, meta))

        # Pass 2: absolute scoring against the frozen active-listing
        # distribution of deal deviations (positive deviation = better
        # value). Falls back to batch z-scores until the first baseline
        # build — which returns a meaningless 50.0 for a single pasted
        # listing, exactly what the baseline path fixes.
        deviations = [r[1] for r in intermediate]
        from apthunt.scoring.baseline import baseline_scores
        scores = baseline_scores(conn, "deal", deviations)
        if scores is None:
            scores = self._deviations_to_scores(deviations)

        results = []
        for i, (listing_id, _, meta) in enumerate(intermediate):
            results.append(ScorerResult(
                listing_id=listing_id,
                score=scores[i],
                components=meta,
            ))

        return results

    # ── Tenure estimation ─────────────────────────────────────

    @staticmethod
    def _estimate_tenure(lst: dict) -> tuple[Optional[float], int]:
        """Estimate median tenant tenure from price_history.

        Algorithm:
          1. Extract all "Listed" event dates (ignoring "Delisted").
          2. Sort chronologically.
          3. Cluster: merge dates within _RELIST_CLUSTER_DAYS into a
             single listing attempt (the user's "don't get fooled by
             5 relists in 3 months" rule).
          4. Gap between clusters ≈ vacancy + lease term.
          5. Subtract _ASSUMED_VACANCY_DAYS → estimated net tenure.
          6. Return (median_months, cycle_count).  None if < 2 clusters.

        Returns:
            (median_months, cycle_count) or (None, 0).
        """
        raw = lst.get("price_history")
        if not raw:
            return None, 0

        if isinstance(raw, str):
            try:
                entries = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None, 0
        elif isinstance(raw, list):
            entries = raw
        else:
            return None, 0

        if not entries:
            return None, 0

        # Collect dates of "Listed" events (not "Delisted")
        listed_dates: list[datetime] = []
        for entry in entries:
            event = (entry.get("event") or "").strip()
            if "listed" in event.lower() and "delisted" not in event.lower():
                date_str = entry.get("date", "")
                for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
                    try:
                        listed_dates.append(datetime.strptime(date_str, fmt))
                        break
                    except ValueError:
                        continue

        if len(listed_dates) < 2:
            return None, 0

        listed_dates.sort()

        # Cluster rapid relists: merge dates within _RELIST_CLUSTER_DAYS
        clusters: list[datetime] = [listed_dates[0]]
        for dt in listed_dates[1:]:
            if (dt - clusters[-1]).days <= _RELIST_CLUSTER_DAYS:
                continue  # same listing attempt, skip
            clusters.append(dt)

        if len(clusters) < 2:
            return None, len(clusters)

        # Gaps between clusters → estimated tenure
        tenures_months: list[float] = []
        for i in range(1, len(clusters)):
            gap_days = (clusters[i] - clusters[i - 1]).days
            net_days = max(0, gap_days - _ASSUMED_VACANCY_DAYS)
            tenures_months.append(net_days / 30.44)  # avg days per month

        median_months = round(statistics.median(tenures_months), 1)
        return median_months, len(clusters)

    # ── Leverage signals (raw_json) ───────────────────────────

    @staticmethod
    def _leverage_signals(
        lst: dict,
    ) -> tuple[Optional[float], Optional[float], Optional[int]]:
        """Parse landlord-leverage signals from the listing's raw_json.

        Returns ``(cut_frac, months_free, cut_recency_days)``:
          * cut_frac — price cut as a fraction of the PRE-cut ask
            (price_delta is an absolute dollar delta, negative = cut;
            prev_ask = price - delta). Clamped to _MAX_CUT_FRAC.
            None when there is no negative delta.
          * months_free — raw_json months_free when > 0, else None.
          * cut_recency_days — whole days since price_changed_at, emitted
            only alongside a cut. None when the timestamp is missing
            or unparseable.

        Degrades gracefully: any missing/malformed field (raw_json absent
        mid-re-download, non-JSON payload, bad timestamp) yields Nones —
        never an exception.
        """
        raw = lst.get("raw_json")
        if not raw:
            return None, None, None
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None, None, None
        elif isinstance(raw, dict):
            data = raw
        else:
            return None, None, None
        if not isinstance(data, dict):
            return None, None, None

        # Months free — already netted into net_effective_price upstream;
        # emitted visibly so the flag/UI layer can show the concession.
        months_free: Optional[float] = None
        try:
            mf = float(data.get("months_free") or 0)
        except (TypeError, ValueError):
            mf = 0.0
        if mf > 0:
            months_free = round(mf, 2)

        # Price cut + recency
        cut_frac: Optional[float] = None
        recency: Optional[int] = None
        try:
            delta = float(data.get("price_delta"))
        except (TypeError, ValueError):
            delta = None
        try:
            price = float(lst.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if delta is not None and delta < 0 and price > 0:
            prev_ask = price - delta  # delta < 0 → pre-cut ask above current
            if prev_ask > 0:
                cut_frac = min(_MAX_CUT_FRAC, -delta / prev_ask)
            ts = data.get("price_changed_at")
            if ts:
                try:
                    changed = datetime.fromisoformat(str(ts)[:10]).date()
                    recency = max(0, (date.today() - changed).days)
                except (ValueError, TypeError):
                    recency = None

        return cut_frac, months_free, recency

    # ── Comp-set builders ───────────────────────────────────────

    def _build_price_comp_sets(self, conn: sqlite3.Connection) -> dict:
        rows = conn.execute(
            "SELECT neighborhood, beds, "
            "  CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END "
            "FROM listings WHERE price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
        ).fetchall()
        comp_sets: dict[tuple, list] = {}
        for neighborhood, beds, cost in rows:
            comp_sets.setdefault((neighborhood, beds), []).append(cost)
        return comp_sets

    def _build_sqft_comp_sets(self, conn: sqlite3.Connection) -> dict:
        """Absolute sqft grouped by (neighborhood, beds)."""
        rows = conn.execute(
            "SELECT neighborhood, beds, sqft "
            "FROM listings "
            "WHERE sqft IS NOT NULL AND sqft > 0 "
            "  AND price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
        ).fetchall()
        comp_sets: dict[tuple, list] = {}
        for neighborhood, beds, sqft in rows:
            comp_sets.setdefault((neighborhood, beds), []).append(sqft)
        return comp_sets

    def _build_ppsqft_comp_sets(self, conn: sqlite3.Connection) -> dict:
        """$/sqft grouped by (neighborhood, beds)."""
        rows = conn.execute(
            "SELECT neighborhood, beds, "
            "  CASE WHEN net_effective_price > 0 THEN net_effective_price ELSE price END, "
            "  sqft "
            "FROM listings "
            "WHERE sqft IS NOT NULL AND sqft > 0 "
            "  AND price IS NOT NULL AND UPPER(status) = 'ACTIVE'"
        ).fetchall()
        comp_sets: dict[tuple, list] = {}
        for neighborhood, beds, cost, sqft in rows:
            if sqft > 0:
                comp_sets.setdefault((neighborhood, beds), []).append(cost / sqft)
        return comp_sets

    @staticmethod
    def _deviations_to_scores(deviations: list[float]) -> list[float]:
        """Z-score normalization: 50 = mean, ±25 per stdev, clamped 0–100."""
        if not deviations:
            return []
        if len(deviations) == 1:
            return [50.0]

        mean = statistics.mean(deviations)
        stdev = statistics.stdev(deviations)

        if stdev == 0:
            return [50.0] * len(deviations)

        scores = []
        for dev in deviations:
            z = (dev - mean) / stdev
            score = 50.0 + z * 25.0
            scores.append(round(max(0.0, min(100.0, score)), 1))
        return scores
