#!/usr/bin/env bash
# One-command demo: seeds data, starts backend (8000) + frontend (3001), opens the browser.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend" && uv run python -m app.data.seed --reset
cd "$ROOT/backend" && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 > "$ROOT/backend/data/backend.log" 2>&1 &
BACK=$!
cd "$ROOT/frontend" && npm run dev > "$ROOT/frontend/frontend.log" 2>&1 &
FRONT=$!
trap 'kill $BACK $FRONT 2>/dev/null || true' EXIT
echo "Backend  → http://localhost:8000/docs"
echo "Frontend → http://localhost:3001"
for i in $(seq 1 60); do curl -sf http://localhost:3001 >/dev/null 2>&1 && break; sleep 1; done
command -v open >/dev/null && open http://localhost:3001 || true
wait
