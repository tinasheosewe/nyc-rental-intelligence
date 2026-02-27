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
import os
from dataclasses import dataclass, field
from typing import Optional

from haversine import haversine as _hav, Unit


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
    return _hav((lat1, lon1), (lat2, lon2), unit=Unit.METERS)


class TransitData:
    """
    In-memory index of subway stations with radius queries.

    Loads from a GTFS stops.txt file. With ~472 stations,
    brute-force Haversine is plenty fast.
    """

    def __init__(self, stops_path: Optional[str] = None):
        self._stations: list[SubwayStation] = []
        if stops_path and os.path.exists(stops_path):
            # Try to find GTFS directory for routes/trips/stop_times
            base = os.path.dirname(stops_path)
            routes_path = os.path.join(base, "routes.txt")
            trips_path = os.path.join(base, "trips.txt")
            stop_times_path = os.path.join(base, "stop_times.txt")
            if all(os.path.exists(p) for p in [routes_path, trips_path, stop_times_path]):
                self.load_with_routes(stops_path, routes_path, trips_path, stop_times_path)
            else:
                self.load_from_file(stops_path)

    def load_with_routes(self, stops_path, routes_path, trips_path, stop_times_path):
        """Load stations and attach routes using full GTFS, aggregating child stop routes to parent stations."""
        # 1. Parse stops.txt (parent and child stops)
        parent_stations: dict[str, SubwayStation] = {}
        child_to_parent: dict[str, str] = {}
        with open(stops_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stop_id = row.get("stop_id", "").strip()
                if not stop_id:
                    continue
                loc_type = row.get("location_type", "").strip()
                parent = row.get("parent_station", "").strip()
                if loc_type == "1" or (loc_type != "0" and not parent):
                    try:
                        lat = float(row["stop_lat"])
                        lon = float(row["stop_lon"])
                    except (KeyError, ValueError):
                        continue
                    name = row.get("stop_name", stop_id).strip()
                    parent_stations[stop_id] = SubwayStation(
                        stop_id=stop_id,
                        name=name,
                        lat=lat,
                        lon=lon,
                    )
                elif parent:
                    child_to_parent[stop_id] = parent
        # 2. Map trip_id → route_id
        trip_to_route = {}
        with open(trips_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trip_id = row.get("trip_id", "").strip()
                route_id = row.get("route_id", "").strip()
                if trip_id and route_id:
                    trip_to_route[trip_id] = route_id
        # 3. Map parent stop_id → set of route_ids (aggregate from children)
        stop_to_routes = {sid: set() for sid in parent_stations}
        with open(stop_times_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stop_id = row.get("stop_id", "").strip()
                trip_id = row.get("trip_id", "").strip()
                if trip_id in trip_to_route:
                    # Map child stop to parent if needed
                    parent = child_to_parent.get(stop_id, stop_id)
                    if parent in stop_to_routes:
                        stop_to_routes[parent].add(trip_to_route[trip_id])
        # 4. Attach routes to each parent station
        for sid, station in parent_stations.items():
            station.routes = sorted(stop_to_routes[sid])
        self._stations = list(parent_stations.values())

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
