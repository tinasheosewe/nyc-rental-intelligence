"""
Geohash utilities — thin wrapper around pygeohash.

Default precision 7 ≈ 153m × 153m cells, matching the
block-level caching strategy in BLOCK_QUALITY_SCORE.md.
"""

from __future__ import annotations

import pygeohash as _pgh


def encode(lat: float, lon: float, precision: int = 7) -> str:
    """Encode (lat, lon) to a geohash string of given precision."""
    return _pgh.encode(lat, lon, precision=precision)


def decode(geohash: str) -> tuple[float, float]:
    """Decode a geohash string to (lat, lon) center of the cell."""
    return _pgh.decode(geohash)


def bounding_box(geohash: str) -> tuple[float, float, float, float]:
    """Return (min_lat, max_lat, min_lon, max_lon) for a geohash cell."""
    bbox = _pgh.get_bounding_box(geohash)
    return (bbox.min_lat, bbox.max_lat, bbox.min_lon, bbox.max_lon)


def neighbors(geohash: str) -> list[str]:
    """Return the 8 adjacent geohash cells (N, NE, E, SE, S, SW, W, NW)."""
    n = _pgh.get_adjacent(geohash, "top")
    s = _pgh.get_adjacent(geohash, "bottom")
    e = _pgh.get_adjacent(geohash, "right")
    w = _pgh.get_adjacent(geohash, "left")
    ne = _pgh.get_adjacent(n, "right")
    se = _pgh.get_adjacent(s, "right")
    sw = _pgh.get_adjacent(s, "left")
    nw = _pgh.get_adjacent(n, "left")
    return [n, ne, e, se, s, sw, w, nw]
