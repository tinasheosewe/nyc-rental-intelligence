"""
CrimeScorer — scores listings by crime density in their surrounding area.

Uses NYPD Complaints (Current YTD) via SODA API with within_circle()
queries cached in BlockCache by geohash.

Scoring:
    Weight by severity: FELONY ×3, MISDEMEANOR ×1.5, VIOLATION ×1.
    Normalize weighted count against a city-wide median.
    Invert so lower crime = higher score.

Status: STUB — implementation pending.
"""

from __future__ import annotations

import sqlite3

from apthunt.data.block_cache import BlockCache
from apthunt.data.soda_client import SodaClient
from apthunt.scoring.base import Scorer, ScorerResult


class CrimeScorer(Scorer):

    def __init__(self, soda: SodaClient, cache: BlockCache):
        self._soda = soda
        self._cache = cache

    @property
    def name(self) -> str:
        return "crime"

    def columns(self) -> dict[str, str]:
        return {
            "crime_felony_count": "INTEGER",
            "crime_misdemeanor_count": "INTEGER",
            "crime_violation_count": "INTEGER",
            "crime_weighted_total": "REAL",
        }

    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        raise NotImplementedError(
            "CrimeScorer is not yet implemented. "
            "See BLOCK_QUALITY_SCORE.md for the query pattern."
        )
