#!/usr/bin/env bash
# build.sh — Render build script for the backend service.
# Installs dependencies and downloads the pre-built SQLite database
# from a hosted copy (avoids rebuilding it on every deploy).
set -euo pipefail

echo "▸ Installing Python dependencies"
pip install -r requirements.txt

DB_FILE="apthunt.db"
DB_URL="https://example.com/apthunt.db"

if [ ! -f "$DB_FILE" ]; then
  echo "▸ Downloading database…"
  curl -L -o "$DB_FILE" "$DB_URL"
  echo "✔ Database downloaded ($(du -h "$DB_FILE" | cut -f1))"
else
  echo "✔ Database already exists, skipping download"
fi
