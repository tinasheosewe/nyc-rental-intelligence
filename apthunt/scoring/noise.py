"""
NoiseScorer — scores listings by 311 quality-of-life complaints density.

Uses 311 Service Requests via SODA API with within_circle() queries
cached in BlockCache by geohash.

Filters to: Noise - Residential, Noise - Street/Sidewalk, Rodent,
HEAT/HOT WATER, Unsanitary Condition.

Scoring:
    Count relevant complaints within 300m in the last 12 months.
    Normalize against city baseline.
    Invert so fewer complaints = higher score.

Status: STUB — implementation pending.
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.soda_client import SodaClient
from apthunt.scoring.base import Scorer, ScorerResult


class NoiseScorer(Scorer):

    def __init__(self, soda: SodaClient, cache: BlockCache):
        self._soda = soda
        self._cache = cache

    @property
    def name(self) -> str:
        return "noise"

    def columns(self) -> dict[str, str]:
        return {
            "noise_complaint_count": "INTEGER",
            "noise_rodent_count": "INTEGER",
            "noise_heat_count": "INTEGER",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        raise NotImplementedError(
            "NoiseScorer is not yet implemented. "
            "See BLOCK_QUALITY_SCORE.md for the query pattern."
        )
