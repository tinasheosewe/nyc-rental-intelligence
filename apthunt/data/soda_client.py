"""
SODA API client for NYC Open Data.

Handles HTTP requests, rate limiting, and provides convenience
methods for within_circle() and bounding-box query patterns.

No authentication required. An optional app_token (free registration
at https://data.cityofnewyork.us) raises the rate limit from
1,000 to 50,000 requests per hour.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Optional

SODA_BASE = "https://data.cityofnewyork.us/resource"

# Known dataset identifiers
DATASETS = {
    "crime": "5uac-w243",     # NYPD complaints (current YTD)
    "crime_hist": "qgea-i56i",  # NYPD complaints (historic)
    "311": "erm2-nwe9",       # 311 service requests
    "pluto": "64uk-42ks",     # PLUTO tax lots
    "dob_violations": "3h2n-5cm9",  # DOB violations
    "dob_permits": "ic3t-wcy2",     # DOB job filings/permits
    "parks": "enfh-gkve",     # NYC Parks properties
}


class SodaClient:
    """
    Thin HTTP client for Socrata SODA API queries.

    Features:
    - Builds $where clauses with within_circle() or bounding box
    - Handles pagination via $offset/$limit
    - Respects rate limits (configurable delay between requests)
    - Optionally accepts an app_token for higher rate limits
    """

    def __init__(
        self,
        app_token: Optional[str] = None,
        delay_sec: float = 0.5,
    ):
        self._app_token = app_token
        self._delay_sec = delay_sec
        self._last_request_time = 0.0

    def query(
        self,
        dataset: str,
        where: str,
        select: str = "*",
        limit: int = 5000,
        order: Optional[str] = None,
    ) -> list[dict]:
        """
        Execute a SODA query and return rows as dicts.

        Args:
            dataset: key from DATASETS dict or a raw 4x4 dataset ID.
            where:   SoQL $where clause.
            select:  SoQL $select clause.
            limit:   max rows to return per request.
            order:   SoQL $order clause.

        Returns:
            List of dicts, one per row.
        """
        dataset_id = DATASETS.get(dataset, dataset)
        url = f"{SODA_BASE}/{dataset_id}.json"

        params: dict[str, str] = {
            "$where": where,
            "$select": select,
            "$limit": str(limit),
        }
        if order:
            params["$order"] = order

        headers = {
            "Accept": "application/json",
        }
        if self._app_token:
            headers["X-App-Token"] = self._app_token

        self._rate_limit()

        query_string = urllib.parse.urlencode(params)
        req = urllib.request.Request(
            f"{url}?{query_string}",
            headers=headers,
        )

        try:
            resp = urllib.request.urlopen(req, timeout=30)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"SODA API error {e.code} for {dataset}: {body}"
            ) from e

    def query_circle(
        self,
        dataset: str,
        geo_column: str,
        lat: float,
        lon: float,
        radius_m: int,
        select: str = "*",
        extra_where: str = "",
        limit: int = 5000,
    ) -> list[dict]:
        """Convenience for within_circle() geo queries."""
        where = f"within_circle({geo_column}, {lat}, {lon}, {radius_m})"
        if extra_where:
            where += f" AND {extra_where}"
        return self.query(dataset, where=where, select=select, limit=limit)

    def query_bbox(
        self,
        dataset: str,
        lat_col: str,
        lon_col: str,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        select: str = "*",
        extra_where: str = "",
        limit: int = 5000,
    ) -> list[dict]:
        """Convenience for bounding-box queries (PLUTO pattern)."""
        where = (
            f"{lat_col} between {min_lat} and {max_lat} "
            f"AND {lon_col} between {min_lon} and {max_lon}"
        )
        if extra_where:
            where += f" AND {extra_where}"
        return self.query(dataset, where=where, select=select, limit=limit)

    def _rate_limit(self):
        """Enforce minimum delay between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._delay_sec:
            time.sleep(self._delay_sec - elapsed)
        self._last_request_time = time.time()
