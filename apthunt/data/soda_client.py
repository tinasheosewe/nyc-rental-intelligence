"""
SODA API client for NYC Open Data — thin wrapper around sodapy.

No authentication required. An optional app_token (free registration
at https://data.cityofnewyork.us) raises the rate limit from
1,000 to 50,000 requests per hour.
"""

from __future__ import annotations

import time
from typing import Optional

from sodapy import Socrata

DOMAIN = "data.cityofnewyork.us"

# Known dataset identifiers
DATASETS = {
    "crime": "5uac-w243",          # NYPD complaints (current YTD)
    "crime_hist": "qgea-i56i",     # NYPD complaints (historic)
    "311": "erm2-nwe9",            # 311 service requests
    "pluto": "64uk-42ks",          # PLUTO tax lots
    "dob_violations": "3h2n-5cm9", # DOB violations
    "dob_permits": "ic3t-wcy2",    # DOB job filings/permits
    "parks": "enfh-gkve",          # NYC Parks properties
}


class SodaClient:
    """
    Thin wrapper around sodapy.Socrata with convenience methods
    for within_circle() and bounding-box query patterns.
    """

    def __init__(
        self,
        app_token: Optional[str] = None,
        delay_sec: float = 0.5,
    ):
        self._client = Socrata(DOMAIN, app_token, timeout=30)
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
        """
        dataset_id = DATASETS.get(dataset, dataset)
        self._rate_limit()

        kwargs = {
            "where": where,
            "select": select,
            "limit": limit,
        }
        if order:
            kwargs["order"] = order

        return self._client.get(dataset_id, **kwargs)

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

    def close(self):
        """Close the underlying Socrata session."""
        self._client.close()

    def _rate_limit(self):
        """Enforce minimum delay between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._delay_sec:
            time.sleep(self._delay_sec - elapsed)
        self._last_request_time = time.time()
