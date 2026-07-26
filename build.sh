#!/usr/bin/env bash
# build.sh — Render build script for the backend service.
# Installs dependencies and downloads the pre-built SQLite database
# from a hosted copy (avoids rebuilding it on every deploy).
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "▸ Installing Python dependencies"
"$PYTHON_BIN" -m pip install -r requirements.txt

DB_FILE="${APTHUNT_DB_FILE:-apthunt.db}"
DB_URL="${APTHUNT_DB_URL:-https://example.com/apthunt.db}"

validate_db() {
  local db_path="$1"
    "$PYTHON_BIN" - "$db_path" <<'PY'
import os
import sqlite3
import sys

db_path = sys.argv[1]

if not os.path.exists(db_path):
    raise SystemExit(f"Database file not found: {db_path}")
if os.path.getsize(db_path) < 1024:
    raise SystemExit(f"Database file is too small to be valid: {db_path}")

conn = sqlite3.connect(db_path)
try:
    quick_check = conn.execute("PRAGMA quick_check").fetchone()
    if not quick_check or quick_check[0] != "ok":
        raise SystemExit(f"SQLite quick_check failed: {quick_check[0] if quick_check else 'unknown'}")

    has_listings = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'listings'"
    ).fetchone()
    if not has_listings:
        raise SystemExit("SQLite database is missing the listings table")

    total, api_visible = conn.execute(
        "SELECT COUNT(*), "
        "SUM(CASE WHEN UPPER(status) = 'ACTIVE' AND lat IS NOT NULL AND lon IS NOT NULL THEN 1 ELSE 0 END) "
        "FROM listings"
    ).fetchone()
    if (total or 0) <= 0:
        raise SystemExit("SQLite database contains zero listings")

    print(f"validated database: total={int(total or 0)} api_visible={int(api_visible or 0)}")
finally:
    conn.close()
PY
}

download_db() {
  local tmp_file="$1"
  echo "▸ Downloading database from ${DB_URL}"
  curl --fail --location --retry 3 --retry-all-errors --output "$tmp_file" "$DB_URL"
}

if [ ! -f "$DB_FILE" ]; then
  TMP_DB="$(mktemp "${TMPDIR:-/tmp}/apthunt.db.XXXXXX")"
  trap 'rm -f "$TMP_DB"' EXIT
  download_db "$TMP_DB"
  validate_db "$TMP_DB"
  mv "$TMP_DB" "$DB_FILE"
  trap - EXIT
  echo "✔ Database downloaded ($(du -h "$DB_FILE" | cut -f1))"
else
  echo "▸ Validating existing database at $DB_FILE"
  validate_db "$DB_FILE"
  echo "✔ Database already exists and passed validation"
fi
