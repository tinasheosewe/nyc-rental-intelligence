#!/usr/bin/env bash
#
# bootstrap_sample.sh — Build a small demo database from the synthetic
# sample listings, scored by the dimensions that are cheap to set up.
#
#   1. python3 ingest.py                 300 fictional listings
#   2. deal + unit_amenities             no downloads at all
#   3. transit, schools, parks,          a few small NYC Open Data / MTA /
#      convenience                       OpenStreetMap tables, fetched on
#                                        first use
#   4. composites                        persisted for the default feed
#
# Step 3 is best-effort: without a network the API still starts, but too
# few dimensions are scored for the web app's default Data Availability
# filter, which has to be set to "Any". Scoring everything is a separate,
# much bigger job — see "Scoring every dimension" in the README.
#
# Usage:
#   ./scripts/bootstrap_sample.sh
#   PYTHON_BIN=.venv/bin/python APTHUNT_DB_PATH=/data/apthunt.db ./scripts/bootstrap_sample.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "▸ Generating synthetic sample listings"
"$PYTHON_BIN" ingest.py --source sample

# --no-fast: the in-memory fast path (numpy/scipy) is built for runs over
# the full dataset; the demo scores straight from SQLite.
echo "▸ Scoring (offline dimensions)"
"$PYTHON_BIN" run_scores.py --no-fast --only deal,unit_amenities

echo "▸ Scoring (dimensions backed by small open-data downloads)"
"$PYTHON_BIN" run_scores.py --no-fast --only transit,schools,parks,convenience \
  || echo "⚠  Open-data scorers skipped — re-run them later with run_scores.py"

echo "▸ Persisting composite scores"
"$PYTHON_BIN" scripts/compute_composites.py

echo "✔ Demo database ready"
