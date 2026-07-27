#!/usr/bin/env python3
"""
Repair degenerate park polygons in ds_parks using OSM geometry.

Problem: 289 parks (>1 acre) in the DPR Parks Properties feed ship with
simplified 4-corner quads — and displaced ones at that (Cooper Park's
quad sits ~100m south of the real footprint, so doorstep listings on its
north side measured 135-222m from the park instead of 13-56m, costing
~21 score points).  The degradation is UPSTREAM: both the SODA API and
the GeoJSON export serve the same quads, so re-downloading cannot fix it.

Fix: fetch leisure=park/garden polygons for NYC from OSM (Overpass),
match each degenerate DPR row by centroid containment + proximity + name
similarity, and swap in the OSM geometry.  DPR attributes (name311,
typecategory, acres) are kept — only the shape is replaced.  Also inserts
Domino Park (privately operated, absent from DPR data) if missing.

Usage:
    python3 scripts/repair_park_geometry.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt.db import get_connection, DB_PATH

BBOX = (40.49, -74.27, 40.92, -73.68)
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
MATCH_MAX_CENTROID_M = 500.0
AREA_RATIO_RANGE = (0.2, 5.0)

_STOPWORDS = {"park", "playground", "garden", "the", "of", "at", "and",
              "msgr", "monsignor", "sq", "square"}


def _fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def n_vertices(geom: dict) -> int:
    n = 0
    if not geom:
        return -1
    raw = geom.get("coordinates", [])
    if geom.get("type") == "MultiPolygon":
        for poly in raw:
            for ring in poly:
                n += len(ring)
    elif geom.get("type") == "Polygon":
        for ring in raw:
            n += len(ring)
    return n


def vertex_centroid(geom: dict) -> tuple:
    lats, lons = [], []
    raw = geom.get("coordinates", [])
    polys = raw if geom.get("type") == "MultiPolygon" else [raw]
    for poly in polys:
        for ring in poly:
            for lon, lat in ring:
                lats.append(lat)
                lons.append(lon)
    return (sum(lats) / len(lats), sum(lons) / len(lons)) if lats else (None, None)


def ring_area_sqm(ring) -> float:
    """Shoelace area of a (lon, lat) ring in m² (equirectangular)."""
    if len(ring) < 3:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    mx = 111_320.0 * math.cos(math.radians(lat0))
    my = 111_320.0
    area = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0] * mx, ring[i][1] * my
        x2, y2 = ring[i + 1][0] * mx, ring[i + 1][1] * my
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def geom_acres(geom: dict) -> float:
    raw = geom.get("coordinates", [])
    polys = raw if geom.get("type") == "MultiPolygon" else [raw]
    total = 0.0
    for poly in polys:
        if poly:
            total += ring_area_sqm(poly[0])
    return total / 4046.86


def point_in_ring(lat: float, lon: float, ring) -> bool:
    """Ray casting; ring is [(lon, lat), ...]."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_int = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_int:
                inside = not inside
        j = i
    return inside


def point_in_geom(lat: float, lon: float, geom: dict) -> bool:
    raw = geom.get("coordinates", [])
    polys = raw if geom.get("type") == "MultiPolygon" else [raw]
    for poly in polys:
        if poly and point_in_ring(lat, lon, poly[0]):
            return True
    return False


def dist_m(lat1, lon1, lat2, lon2) -> float:
    mx = 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat1 - lat2) * 111_320.0, (lon1 - lon2) * mx)


def name_tokens(name: str) -> set:
    toks = set(re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split())
    return toks - _STOPWORDS


def name_sim(a: str, b: str) -> float:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


# ── OSM fetch ────────────────────────────────────────────────────────

def fetch_osm_parks() -> list:
    """OSM leisure=park/garden polygons in the NYC bbox.

    Returns [{name, geom (GeoJSON MultiPolygon), centroid, acres}].
    Ways become single-ring polygons; relations contribute each CLOSED
    outer-role member as its own ring (open members are stitched when
    endpoints chain, else skipped — good enough for matching, and we
    only adopt geometry from confident matches).
    """
    s, w, n, e = BBOX
    query = f"""[out:json][timeout:240];
(
  way["leisure"~"^(park|garden|playground|recreation_ground)$"]({s},{w},{n},{e});
  relation["leisure"~"^(park|garden|playground|recreation_ground)$"]({s},{w},{n},{e});
);
out tags geom;"""
    data = None
    for url in OVERPASS_MIRRORS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    url, data=query.encode(),
                    headers={"User-Agent": "apthunt-park-repair/1.0"})
                with urllib.request.urlopen(req, timeout=300) as r:
                    data = json.loads(r.read())
                break
            except Exception as exc:
                print(f"  overpass {url} attempt {attempt+1}: {exc}")
                time.sleep(15)
        if data:
            break
    if not data:
        raise RuntimeError("all Overpass mirrors failed")

    parks = []
    for el in data.get("elements", []):
        name = (el.get("tags") or {}).get("name", "")
        rings = []
        if el["type"] == "way" and el.get("geometry"):
            ring = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if len(ring) >= 4:
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                rings.append(ring)
        elif el["type"] == "relation":
            open_segs = []
            for m in el.get("members", []):
                if m.get("role") not in ("outer", "") or not m.get("geometry"):
                    continue
                seg = [(p["lon"], p["lat"]) for p in m["geometry"]]
                if len(seg) >= 2 and seg[0] == seg[-1] and len(seg) >= 4:
                    rings.append(seg)
                elif len(seg) >= 2:
                    open_segs.append(seg)
            # stitch open segments into rings by chaining endpoints
            while open_segs:
                chain = open_segs.pop(0)
                progress = True
                while progress and chain[0] != chain[-1]:
                    progress = False
                    for i, seg in enumerate(open_segs):
                        if seg[0] == chain[-1]:
                            chain += seg[1:]
                        elif seg[-1] == chain[-1]:
                            chain += list(reversed(seg))[1:]
                        elif seg[-1] == chain[0]:
                            chain = seg[:-1] + chain
                        elif seg[0] == chain[0]:
                            chain = list(reversed(seg))[:-1] + chain
                        else:
                            continue
                        open_segs.pop(i)
                        progress = True
                        break
                if len(chain) >= 4 and chain[0] == chain[-1]:
                    rings.append(chain)
        if not rings:
            continue
        # Largest ring = outer boundary proxy for matching/area
        rings.sort(key=ring_area_sqm, reverse=True)
        geom = {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}
        clat, clon = vertex_centroid(geom)
        parks.append({
            "name": name,
            "geom": geom,
            "centroid": (clat, clon),
            "acres": geom_acres(geom),
        })
    return parks


# ── Matching & repair ────────────────────────────────────────────────

def match(row, osm_parks):
    """Best OSM polygon for a degenerate DPR row, or None."""
    name, acres, clat, clon = row["name311"], row["acres"], row["clat"], row["clon"]
    best, best_score = None, 0.0
    for op in osm_parks:
        oclat, oclon = op["centroid"]
        if oclat is None:
            continue
        d = dist_m(clat, clon, oclat, oclon)
        if d > MATCH_MAX_CENTROID_M:
            continue
        if acres > 0 and op["acres"] > 0:
            ratio = op["acres"] / acres
            if not (AREA_RATIO_RANGE[0] <= ratio <= AREA_RATIO_RANGE[1]):
                continue
        contains = point_in_geom(clat, clon, op["geom"])
        sim = name_sim(name, op["name"])
        score = (2.0 if contains else 0.0) + 1.5 * sim + (1.0 - d / MATCH_MAX_CENTROID_M)
        if score > best_score:
            best, best_score = op, score
    # Accept only confident matches
    if best is None:
        return None
    contains = point_in_geom(clat, clon, best["geom"])
    sim = name_sim(row["name311"], best["name"])
    if contains or (sim >= 0.5 and dist_m(clat, clon, *best["centroid"]) < 300):
        return best
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = get_connection(DB_PATH)
    rows = conn.execute(
        "SELECT rowid, name311, typecategory, acres, multipolygon, "
        "centroid_lat, centroid_lon FROM ds_parks WHERE multipolygon IS NOT NULL"
    ).fetchall()

    degenerate = []
    for r in rows:
        geom = json.loads(r[4])
        acres = _fnum(r[3])
        if acres > 1.0 and 0 <= n_vertices(geom) < 12:
            degenerate.append({
                "rowid": r[0], "name311": r[1], "acres": acres,
                "clat": _fnum(r[5]), "clon": _fnum(r[6]),
            })
    print(f"degenerate parks (>1 acre, <12 vertices): {len(degenerate)}")

    print("fetching OSM park polygons (Overpass)...")
    osm_parks = fetch_osm_parks()
    print(f"OSM polygons: {len(osm_parks)}")

    repaired, unmatched = 0, []
    for row in degenerate:
        m = match(row, osm_parks)
        if m is None:
            unmatched.append(row["name311"])
            continue
        clat, clon = vertex_centroid(m["geom"])
        if not args.dry_run:
            conn.execute(
                "UPDATE ds_parks SET multipolygon = ?, centroid_lat = ?, "
                "centroid_lon = ? WHERE rowid = ?",
                (json.dumps(m["geom"]), clat, clon, row["rowid"]),
            )
        repaired += 1
        if row["name311"] in ("Cooper Park", "Sunset Park", "Msgr. McGolrick Park"):
            print(f"  ✓ {row['name311']}: {n_vertices(m['geom'])} verts "
                  f"(OSM '{m['name']}', {m['acres']:.1f}ac vs DPR {row['acres']:.1f}ac)")

    # Domino Park: absent from DPR data entirely
    has_domino = conn.execute(
        "SELECT COUNT(*) FROM ds_parks WHERE name311 LIKE '%Domino%'"
    ).fetchone()[0]
    if not has_domino:
        cands = [p for p in osm_parks if "domino" in (p["name"] or "").lower()]
        if cands:
            dp = max(cands, key=lambda p: p["acres"])
            clat, clon = vertex_centroid(dp["geom"])
            if not args.dry_run:
                conn.execute(
                    "INSERT INTO ds_parks (name311, typecategory, acres, "
                    "multipolygon, centroid_lat, centroid_lon) VALUES (?,?,?,?,?,?)",
                    ("Domino Park", "Neighborhood Park", round(dp["acres"], 3),
                     json.dumps(dp["geom"]), clat, clon),
                )
            print(f"  + Domino Park inserted from OSM ({dp['acres']:.1f}ac)")

    if not args.dry_run:
        conn.commit()
    print(f"repaired {repaired}/{len(degenerate)}; unmatched: {len(unmatched)}")
    if unmatched:
        print("  unmatched (left as-is):", ", ".join(sorted(unmatched)[:20]),
              "..." if len(unmatched) > 20 else "")
    conn.close()


if __name__ == "__main__":
    main()
