#!/usr/bin/env bash
#
# start.sh — Kill stale processes and start backend + frontend dev servers.
#
# Usage:
#   ./start.sh          # starts both servers
#   ./start.sh --kill   # only kill, don't restart
#
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# ── Activate venv (even if script is run outside it) ────────────
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if [[ -f "$DIR/.venv/bin/activate" ]]; then
    echo "▸ Activating .venv"
    source "$DIR/.venv/bin/activate"
  else
    echo "⚠  No .venv found at $DIR/.venv — running with system Python"
  fi
fi

# ── Kill anything on ports 8000 / 3000 ──────────────────────────
echo "▸ Clearing ports 8000 and 3000"
lsof -ti :8000 | xargs kill 2>/dev/null || true
lsof -ti :3000 | xargs kill 2>/dev/null || true
sleep 1

if [[ "${1:-}" == "--kill" ]]; then
  echo "✔ Ports cleared. Exiting (--kill mode)."
  exit 0
fi

# ── Start backend (FastAPI + uvicorn) ───────────────────────────
echo "▸ Starting backend on :8000"
python3 -m uvicorn api.app:app --reload --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

# ── Start frontend (Next.js) ───────────────────────────────────
echo "▸ Starting frontend on :3000"
cd "$DIR/frontend"
npm run dev &
FRONTEND_PID=$!
cd "$DIR"

# ── Wait + clean shutdown on Ctrl-C ────────────────────────────
cleanup() {
  echo ""
  echo "▸ Shutting down…"
  kill $BACKEND_PID 2>/dev/null || true
  kill $FRONTEND_PID 2>/dev/null || true
  wait 2>/dev/null
  echo "✔ Done."
}
trap cleanup EXIT INT TERM

# ── Health-check: wait for backend to respond ──────────────────
echo "▸ Waiting for backend …"
for i in $(seq 1 20); do
  if curl -sf http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
    echo "✔ Backend  → http://localhost:8000  (up)"
    break
  fi
  # If the process already died, fail fast
  if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "✖ Backend process died. Check logs above."
    exit 1
  fi
  sleep 1
done

# If we exhausted retries
if ! curl -sf http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
  echo "✖ Backend failed to respond after 20 s. Check logs above."
  exit 1
fi

echo "✔ Frontend → http://localhost:3000"
echo "  Press Ctrl-C to stop both servers."
echo ""

# Block until Ctrl-C (or either process exits).
wait
