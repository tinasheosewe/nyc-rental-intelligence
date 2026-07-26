#!/usr/bin/env python3
"""
Repair listing geocodes against the PLUTO address gazetteer.

Listing sources sometimes ship fuzzed or wrong coordinates (one listing
arrived ~250m west of its actual lot, so every building- and
block-level signal was computed for a stranger's rowhouse on another
street). PLUTO knows the true coordinates of every addressed lot —
when a listing's street address matches a PLUTO lot and the coordinates
disagree by more than REPAIR_THRESHOLD_M, trust the address.

Repaired rows get geocode_fixed=1 and need re-scoring (their geohash
changed, so all block caches miss naturally).

Usage:
    python3 scripts/repair_geocodes.py            # repair + report
    python3 scripts/repair_geocodes.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt.db import get_connection, DB_PATH
from haversine import haversine, Unit

REPAIR_THRESHOLD_M = 80.0

SUFFIX = {"STREET": "ST", "AVENUE": "AVE", "PLACE": "PL", "ROAD": "RD",
          "BOULEVARD": "BLVD", "DRIVE": "DR", "LANE": "LN", "COURT": "CT",
          "TERRACE": "TER", "PARKWAY": "PKWY", "EAST": "E", "WEST": "W",
          "NORTH": "N", "SOUTH": "S"}
BORO = {"manhattan": "1", "bronx": "2", "brooklyn": "3", "queens": "4",
        "staten-island": "5"}


def norm_street(s: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", "", (s or "").upper()).strip()
    return " ".join(SUFFIX.get(w, w) for w in s.split())


def parse(addr: str):
    m = re.match(r"^\s*([0-9][0-9\-]*)\s+(.+)$", addr or "")
    if not m:
        return None, None
    return m.group(1).replace(" ", ""), norm_street(m.group(2))


def build_gazetteer(conn) -> dict:
    gaz = {}
    for bbl, addr, la, lo in conn.execute(
        "SELECT bbl, address, latitude, longitude FROM ds_pluto "
        "WHERE address IS NOT NULL AND latitude IS NOT NULL"
    ):
        num, street = parse(addr)
        if not num:
            continue
        try:
            b = str(int(float(bbl)))[0]
        except (TypeError, ValueError):
            continue
        gaz[(b, street, num)] = (float(la), float(lo))
        # Address ranges like "352-354 MAIN STREET": index both endpoints
        if "-" in num:
            for endpoint in num.split("-"):
                if endpoint:
                    gaz.setdefault((b, street, endpoint), (float(la), float(lo)))
    return gaz


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", type=str, default=DB_PATH)
    args = ap.parse_args()

    conn = get_connection(args.db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(listings)")}
    if "geocode_fixed" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN geocode_fixed INTEGER")

    gaz = build_gazetteer(conn)
    print(f"gazetteer: {len(gaz):,} address-keyed lots")

    repairs = []
    # All listings with coordinates — including INACTIVE (detail pages and
    # ad-hoc scoring serve them too).
    for lid, addr, boro, la, lo in conn.execute(
        "SELECT id, address, borough, lat, lon FROM listings "
        "WHERE lat IS NOT NULL AND address IS NOT NULL"
    ):
        num, street = parse(addr)
        b = BORO.get(boro or "")
        if not num or not b:
            continue
        hit = gaz.get((b, street, num))
        if not hit:
            continue
        d = haversine((la, lo), hit, unit=Unit.METERS)
        if d > REPAIR_THRESHOLD_M:
            repairs.append((lid, addr, d, hit))

    print(f"repairs needed (> {REPAIR_THRESHOLD_M:.0f}m): {len(repairs)}")
    for lid, addr, d, _ in sorted(repairs, key=lambda r: -r[2])[:10]:
        print(f"   {d:6.0f}m  {addr}")

    if args.dry_run or not repairs:
        conn.close()
        return

    conn.executemany(
        "UPDATE listings SET lat=?, lon=?, geocode_fixed=1 WHERE id=?",
        [(hit[0], hit[1], lid) for lid, _, _, hit in repairs],
    )
    conn.commit()
    print(f"repaired {len(repairs)} listings — re-score them:")
    print("   python3 run_scores.py --ids " +
          ",".join(lid for lid, _, _, _ in repairs[:5]) + (",..." if len(repairs) > 5 else ""))
    # Emit the full id list for the caller
    with open("/tmp/apthunt_repaired_ids.txt", "w") as f:
        f.write(",".join(lid for lid, _, _, _ in repairs))
    conn.close()


if __name__ == "__main__":
    main()
