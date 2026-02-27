"""
Pure-Python geohash encoder/decoder.

Encodes (lat, lon) to a base32 geohash string.
Decodes a geohash string to (lat, lon) center point.

Default precision 7 ≈ 153m × 153m cells, matching the
block-level caching strategy in BLOCK_QUALITY_SCORE.md.
"""

from __future__ import annotations

BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_DECODE_MAP = {c: i for i, c in enumerate(BASE32)}


def encode(lat: float, lon: float, precision: int = 7) -> str:
    """Encode (lat, lon) to a geohash string of given precision."""
    lat_range = (-90.0, 90.0)
    lon_range = (-180.0, 180.0)
    bits = 0
    bit_count = 0
    is_lon = True
    result = []

    while len(result) < precision:
        if is_lon:
            mid = (lon_range[0] + lon_range[1]) / 2
            if lon >= mid:
                bits = (bits << 1) | 1
                lon_range = (mid, lon_range[1])
            else:
                bits = bits << 1
                lon_range = (lon_range[0], mid)
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat >= mid:
                bits = (bits << 1) | 1
                lat_range = (mid, lat_range[1])
            else:
                bits = bits << 1
                lat_range = (lat_range[0], mid)

        is_lon = not is_lon
        bit_count += 1

        if bit_count == 5:
            result.append(BASE32[bits])
            bits = 0
            bit_count = 0

    return "".join(result)


def decode(geohash: str) -> tuple[float, float]:
    """Decode a geohash string to (lat, lon) center of the cell."""
    bbox = bounding_box(geohash)
    return (
        (bbox[0] + bbox[1]) / 2,
        (bbox[2] + bbox[3]) / 2,
    )


def bounding_box(geohash: str) -> tuple[float, float, float, float]:
    """Return (min_lat, max_lat, min_lon, max_lon) for a geohash cell."""
    lat_range = [-90.0, 90.0]
    lon_range = [-180.0, 180.0]
    is_lon = True

    for char in geohash:
        val = _DECODE_MAP[char]
        for bit in range(4, -1, -1):
            if is_lon:
                mid = (lon_range[0] + lon_range[1]) / 2
                if val & (1 << bit):
                    lon_range[0] = mid
                else:
                    lon_range[1] = mid
            else:
                mid = (lat_range[0] + lat_range[1]) / 2
                if val & (1 << bit):
                    lat_range[0] = mid
                else:
                    lat_range[1] = mid
            is_lon = not is_lon

    return (lat_range[0], lat_range[1], lon_range[0], lon_range[1])


def neighbors(geohash: str) -> list[str]:
    """Return the 8 adjacent geohash cells (N, NE, E, SE, S, SW, W, NW)."""
    bbox = bounding_box(geohash)
    lat_center = (bbox[0] + bbox[1]) / 2
    lon_center = (bbox[2] + bbox[3]) / 2
    lat_delta = bbox[1] - bbox[0]
    lon_delta = bbox[3] - bbox[2]
    precision = len(geohash)

    offsets = [
        (lat_delta, 0),           # N
        (lat_delta, lon_delta),   # NE
        (0, lon_delta),           # E
        (-lat_delta, lon_delta),  # SE
        (-lat_delta, 0),          # S
        (-lat_delta, -lon_delta), # SW
        (0, -lon_delta),          # W
        (lat_delta, -lon_delta),  # NW
    ]

    result = []
    for dlat, dlon in offsets:
        result.append(encode(lat_center + dlat, lon_center + dlon, precision))
    return result
