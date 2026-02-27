"""
Scorer interface and result type.

Every scorer implements the Scorer ABC. The ScoringEngine
calls score() on each registered scorer and writes the
results back to the listings table.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
import sqlite3


@dataclass
class ScorerResult:
    """Result from a single scorer for a single listing."""
    listing_id: str
    score: float
    components: dict[str, Any] = field(default_factory=dict)


class Scorer(ABC):
    """
    Base interface for all scorers.

    Single Responsibility: each Scorer computes exactly one signal.
    Open/Closed: new scorers implement this ABC without touching
    the engine or existing scorers.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier used as column prefix (e.g. 'deal' → deal_score)."""
        ...

    @abstractmethod
    def score(
        self,
        conn: sqlite3.Connection,
        listings: list[dict],
    ) -> list[ScorerResult]:
        """
        Score a batch of listings.

        Each listing dict contains at minimum: id, lat, lon, price,
        beds, neighborhood, borough, net_effective_price, geohash.

        Returns one ScorerResult per listing.
        """
        ...

    def columns(self) -> dict[str, str]:
        """Extra SQLite columns this scorer writes, as {name: type}.

        The engine auto-creates {self.name}_score REAL.
        This declares additional metadata columns.
        """
        return {}
