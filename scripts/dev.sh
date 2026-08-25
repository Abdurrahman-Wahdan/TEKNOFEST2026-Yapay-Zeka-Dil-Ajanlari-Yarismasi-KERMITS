#!/usr/bin/env bash
set -euo pipefail

TF26_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TF26_ROOT"

if [ ! -x .venv/bin/uvicorn ] || [ ! -d UI/node_modules ] || [ ! -f .env ]; then
  echo "Run 'bash scripts/setup_local.sh' first."
  exit 1
fi

docker compose up -d postgres qdrant searxng
.venv/bin/alembic upgrade head

shutdown_tf26() {
  trap - INT TERM EXIT
  kill "$TF26_API_PID" "$TF26_UI_PID" 2>/dev/null || true
  wait "$TF26_API_PID" "$TF26_UI_PID" 2>/dev/null || true
}
trap shutdown_tf26 INT TERM EXIT

.venv/bin/uvicorn api.main:app --reload --host 127.0.0.1 --port 8000 &
TF26_API_PID=$!
npm --prefix UI run dev -- --hostname 127.0.0.1 &
TF26_UI_PID=$!

echo "TF26 API: http://127.0.0.1:8000/docs"
echo "TF26 UI:  http://127.0.0.1:3000/tr"
echo "Press Ctrl+C to stop the API and UI. Docker services stay running."

wait -n "$TF26_API_PID" "$TF26_UI_PID"

