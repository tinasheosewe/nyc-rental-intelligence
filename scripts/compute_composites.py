#!/usr/bin/env python3
"""
Persist the default-priority composite score for every active listing.

The feed endpoint sorts by composite on every request; computing it in
Python for every active row made the default feed take ~3s. This
script materializes the DEFAULT-priority composite (compute_composite
with default priorities, exclude_schools=True — matching the feed display) into a `composite_score`
REAL column so the API can ORDER BY it in SQL.

Meant to run after run_scores.py refreshes the per-dimension scores:

    python3 run_scores.py && python3 scripts/compute_composites.py

Custom-priority requests are unaffected — they still compute per-request.
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apthunt.db import get_connection, DB_PATH
from api.composite import compute_composite, apply_dealbreaker_cap, SCORE_KEYS


def ensure_column(conn) -> None:
    """Add the composite_score column if it doesn't exist yet."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
    if "composite_score" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN composite_score REAL")
        print("Added composite_score REAL column to listings.")


def main():
    conn = get_connection(DB_PATH)
    try:
        ensure_column(conn)

        score_cols = ", ".join(f"{k}_score" for k in SCORE_KEYS)
        rows = conn.execute(
            f"SELECT id, {score_cols} FROM listings WHERE UPPER(status) = 'ACTIVE'"
        ).fetchall()

        t0 = time.time()
        raws: list[tuple[str, float]] = []
        score_dicts: list[dict] = []
        for row in rows:
            scores = {
                key: round(float(row[f"{key}_score"]), 1)
                if row[f"{key}_score"] is not None
                else None
                for key in SCORE_KEYS
            }
            composite, _ = compute_composite(scores, None, exclude_schools=True)
            raws.append((row["id"], composite))
            score_dicts.append(scores)

        # Freeze the raw-composite distribution, then store each listing's
        # PERCENTILE within it as composite_score. Raw composites are means
        # of many percentiles and concentrate in a narrow band (citywide max
        # ~80 → everything reads "Good"); the percentile restores the full
        # 0-100 range: 92 = better overall than 92% of active NYC listings.
        from apthunt.scoring import baseline as bl

        raw_values = [r[1] for r in raws]
        bl.store_baseline(
            conn, "__composite__", raw_values,
            reverse=False, zero_is_perfect=False,
        )
        bl.clear_cache()
        pcts = bl.baseline_scores(conn, "__composite__", raw_values)

        # Dealbreaker caps apply on the PERCENTILE scale (capping raw
        # composites before percentile-izing displayed capped listings at
        # the 67th percentile — above the cap's intent).
        capped = []
        for (lid, _), p, srow in zip(raws, pcts, score_dicts):
            capped.append((apply_dealbreaker_cap(p, srow), lid))
        conn.executemany(
            "UPDATE listings SET composite_score = ? WHERE id = ?", capped,
        )
        conn.commit()
        print(f"Updated composite_score (percentile-ized) for {len(raws)} "
              f"active listings in {time.time() - t0:.1f}s "
              f"(raw range {min(raw_values):.1f}-{max(raw_values):.1f})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
