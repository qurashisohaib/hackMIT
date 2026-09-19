#!/usr/bin/env bash
# One-command offline demo; each server owns a process group for complete cleanup.
set -euo pipefail
set -m
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export CFO_LLM_ENABLED="${CFO_LLM_ENABLED:-false}"
API_PORT="${CFO_API_PORT:-8000}"
export CFO_BACKEND_URL="http://127.0.0.1:$API_PORT"
BACK=""
FRONT=""
cleanup() {
  trap - EXIT INT TERM
  for pid in "$BACK" "$FRONT"; do
    if [[ -n "$pid" ]]; then kill -TERM -- "-$pid" 2>/dev/null || true; fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ready() {
  local url="$1" pid="$2" name="$3"
  for ((i = 0; i < 90; i++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$name exited before readiness; check its log." >&2
      return 1
    fi
    if curl --max-time 2 -fsS "$url" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo "$name did not become ready within 90 attempts; check its log." >&2
  return 1
}
command -v curl >/dev/null
(cd "$ROOT/backend" && uv run --frozen python -m app.data.seed --reset)
(cd "$ROOT/backend" && exec uv run --frozen uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT") > "$ROOT/backend/data/backend.log" 2>&1 &
BACK=$!
ready "$CFO_BACKEND_URL/api/health" "$BACK" Backend
(cd "$ROOT/frontend" && exec npm run dev) > "$ROOT/frontend/frontend.log" 2>&1 &
FRONT=$!
ready "http://127.0.0.1:3001/api/health" "$FRONT" Frontend
echo "Backend  → http://localhost:$API_PORT/docs"
echo "Frontend → http://localhost:3001"
echo "Ready. Ctrl+C stops both servers."
while kill -0 "$BACK" 2>/dev/null && kill -0 "$FRONT" 2>/dev/null; do sleep 1; done
echo "A demo server stopped unexpectedly; check backend/data/backend.log and frontend/frontend.log." >&2
exit 1
