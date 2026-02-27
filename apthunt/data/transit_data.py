"""
MTA subway station data loader.

Loads stops.txt from a local copy of the MTA GTFS feed and provides
spatial lookups: stations within a radius of a point.

Since subway stations don't move, this data is loaded once and
cached in-memory for the lifetime of the process. The stops.txt
file is ~30 KB and can be committed to the repo.

GTFS source: http://web.mta.info/developers/data/nyct/subway/google_transit.zip
Extract stops.txt from the archive.
"""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SubwayStation:
    """A single subway station with its location and served routes."""
    stop_id: str
    name: str
    lat: float
    lon: float
    routes: list[str] = field(default_factory=list)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in meters between two lat/lon points."""
    R = 6_371_000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class TransitData:
    """
    In-memory index of subway stations with radius queries.

    Loads from a GTFS stops.txt file. With ~472 stations,
    brute-force Haversine is plenty fast.
    """

    def __init__(self, stops_path: Optional[str] = None):
        self._stations: list[SubwayStation] = []
        if stops_path and os.path.exists(stops_path):
            self.load_from_file(stops_path)

    @property
    def station_count(self) -> int:
        return len(self._stations)

    def load_from_file(self, path: str):
        """
        Load stops.txt (GTFS format).

        GTFS stops.txt has columns: stop_id, stop_name, stop_lat, stop_lon,
        location_type, parent_station, ...

        We keep only parent stations (location_type == 1) or, if that field
        is absent, stations without a parent_station value. This avoids
        double-counting platform-level entries.
        """
        raw: dict[str, SubwayStation] = {}

        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stop_id = row.get("stop_id", "").strip()
                if not stop_id:
                    continue

                # GTFS location_type: 1 = station, 0 = platform/stop
                loc_type = row.get("location_type", "").strip()
                parent = row.get("parent_station", "").strip()

                # Keep parent stations (location_type=1) OR entries with
                # no parent (standalone stops in simpler feeds)
                if loc_type == "1" or (loc_type != "0" and not parent):
                    try:
                        lat = float(row["stop_lat"])
                        lon = float(row["stop_lon"])
                    except (KeyError, ValueError):
                        continue
                    name = row.get("stop_name", stop_id).strip()
                    raw[stop_id] = SubwayStation(
                        stop_id=stop_id,
                        name=name,
                        lat=lat,
                        lon=lon,
                    )

        # If the feed has no location_type at all, fall back to
        # deduplication by name (take first occurrence)
        if not raw:
            seen_names: set[str] = set()
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    stop_id = row.get("stop_id", "").strip()
                    name = row.get("stop_name", "").strip()
                    if name in seen_names or not stop_id:
                        continue
                    seen_names.add(name)
                    try:
                        lat = float(row["stop_lat"])
                        lon = float(row["stop_lon"])
                    except (KeyError, ValueError):
                        continue
                    raw[stop_id] = SubwayStation(
                        stop_id=stop_id,
                        name=name,
                        lat=lat,
                        lon=lon,
                    )

        self._stations = list(raw.values())

    def load_from_rows(self, rows: list[dict]):
        """Load stations from a list of dicts (for testing)."""
        self._stations = [
            SubwayStation(
                stop_id=r["stop_id"],
                name=r.get("name", r["stop_id"]),
                lat=r["lat"],
                lon=r["lon"],
                routes=r.get("routes", []),
            )
            for r in rows
        ]

    def stations_within(
        self,
        lat: float,
        lon: float,
        radius_m: float = 800,
    ) -> list[SubwayStation]:
        """Return stations within radius_m meters of (lat, lon)."""
        hits = []
        for s in self._stations:
            if _haversine(lat, lon, s.lat, s.lon) <= radius_m:
                hits.append(s)
        return hits

    def nearest(self, lat: float, lon: float) -> tuple[Optional[SubwayStation], float]:
        """Return (nearest station, distance in meters). (None, inf) if empty."""
        best: Optional[SubwayStation] = None
        best_dist = float("inf")
        for s in self._stations:
            d = _haversine(lat, lon, s.lat, s.lon)
            if d < best_dist:
                best_dist = d
                best = s
        return best, best_dist

    def station_count_and_routes(
        self,
        lat: float,
        lon: float,
        radius_m: float = 800,
    ) -> tuple[int, int]:
        """Return (station_count, total_unique_routes) within radius."""
        stations = self.stations_within(lat, lon, radius_m)
        unique_routes: set[str] = set()
        for s in stations:
            unique_routes.update(s.routes)
        return len(stations), len(unique_routes)
