"""
UnitAmenitiesScorer — scores listings by in-unit / in-building amenities.

Parses the ``amenities`` JSON column populated from the listing source's
amenity list.  When data is available, amenities are
categorised into tiers and scored by weighted count.

Premium amenities (washer/dryer in-unit, dishwasher, private outdoor
space) are weighted higher than standard ones (doorman, elevator, gym).

When no amenity data is available for a listing the scorer returns
``None`` so the dimension is excluded from group averages.

Output columns:
    unit_amenities_premium   INTEGER — count of premium amenity matches
    unit_amenities_standard  INTEGER — count of standard amenity matches
    unit_amenities_total     INTEGER — weighted total
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from apthunt.scoring.base import Scorer, ScorerResult
from apthunt.scoring.utils import percentile_scores

log = logging.getLogger(__name__)

# ── Amenity classification ──────────────────────────────────────

# Keywords mapped to (tier, weight).
# Matching is case-insensitive substring.
_AMENITY_TIERS: list[tuple[str, str, int]] = [
    # keyword fragment,     tier,       weight
    ("washer",              "premium",  3),
    ("dryer",               "premium",  3),
    ("dishwasher",          "premium",  3),
    ("private outdoor",     "premium",  3),
    ("balcony",             "premium",  3),
    ("terrace",             "premium",  3),
    ("roof",                "premium",  2),
    ("central a/c",         "premium",  2),
    ("central air",         "premium",  2),
    ("doorman",             "standard", 2),
    ("concierge",           "standard", 2),
    ("elevator",            "standard", 2),
    ("gym",                 "standard", 2),
    ("fitness",             "standard", 2),
    ("pool",                "standard", 2),
    ("garage",              "standard", 1),
    ("parking",             "standard", 1),
    ("bike",                "standard", 1),
    ("storage",             "standard", 1),
    ("laundry",             "standard", 1),
    ("pet",                 "standard", 1),
    ("lounge",              "standard", 1),
    ("courtyard",           "standard", 1),
    ("garden",              "standard", 1),
]


def _classify_amenities(amenities: list[str]) -> dict[str, int]:
    """Classify a list of amenity strings into premium/standard counts + weighted total."""
    premium = 0
    standard = 0
    weighted = 0
    matched: set[str] = set()

    for amenity_str in amenities:
        lower = amenity_str.lower()
        for keyword, tier, weight in _AMENITY_TIERS:
            if keyword in lower and keyword not in matched:
                matched.add(keyword)
                if tier == "premium":
                    premium += 1
                else:
                    standard += 1
                weighted += weight

    return {
        "unit_amenities_premium": premium,
        "unit_amenities_standard": standard,
        "unit_amenities_total": weighted,
    }


class UnitAmenitiesScorer(Scorer):

    @property
    def name(self) -> str:
        return "unit_amenities"

    def columns(self) -> dict[str, str]:
        return {
            "unit_amenities_premium": "INTEGER",
            "unit_amenities_standard": "INTEGER",
            "unit_amenities_total": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        # Parse amenities JSON for each listing
        parsed: list[dict[str, int] | None] = []
        for lst in listings:
            raw = lst.get("amenities")
            if not raw:
                parsed.append(None)
                continue
            try:
                amenity_list = json.loads(raw)
                if isinstance(amenity_list, list) and len(amenity_list) > 0:
                    parsed.append(_classify_amenities(amenity_list))
                else:
                    parsed.append(None)
            except (json.JSONDecodeError, TypeError):
                parsed.append(None)

        # Collect weighted totals for listings that have data
        has_data = [(i, p) for i, p in enumerate(parsed) if p is not None]

        if not has_data:
            # No amenity data at all — return None scores
            return [
                ScorerResult(
                    listing_id=lst["id"],
                    score=None,
                    components={},
                )
                for lst in listings
            ]

        # Percentile-rank only listings with data
        raw_totals = [p["unit_amenities_total"] for _, p in has_data]
        pct_scores = percentile_scores(raw_totals, reverse=False)

        # Build score map for listings with data
        score_map: dict[int, float] = {}
        for (idx, _), pct in zip(has_data, pct_scores):
            score_map[idx] = pct

        results: list[ScorerResult] = []
        for i, lst in enumerate(listings):
            p = parsed[i]
            if p is None:
                results.append(
                    ScorerResult(
                        listing_id=lst["id"],
                        score=None,
                        components={},
                    )
                )
            else:
                results.append(
                    ScorerResult(
                        listing_id=lst["id"],
                        score=score_map[i],
                        components=p,
                    )
                )

        return results
