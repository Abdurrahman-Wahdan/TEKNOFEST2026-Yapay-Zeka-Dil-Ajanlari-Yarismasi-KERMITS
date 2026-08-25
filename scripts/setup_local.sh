#!/usr/bin/env bash
set -euo pipefail

TF26_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$TF26_ROOT"

for command_name in python3 node npm docker openssl; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name"
    exit 1
  fi
done

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required (the 'docker compose' command)."
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 13))'; then
  echo "Python 3.13 is required. Current: $(python3 --version 2>&1)"
  exit 1
fi

if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 20 || (major === 20 && minor >= 9) ? 0 : 1)'; then
  echo "Node.js 20.9 or newer is required. Current: $(node --version 2>&1)"
  exit 1
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
npm --prefix UI ci

if [ ! -f .env ]; then
  cp .env.example .env
  TF26_JWT_SECRET="$(openssl rand -hex 32)"
  TF26_JWT_SECRET="$TF26_JWT_SECRET" .venv/bin/python -c '
import os
from pathlib import Path

path = Path(".env")
text = path.read_text("utf-8")
text = text.replace("API_JWT_SECRET=", f"API_JWT_SECRET={os.environ[\"TF26_JWT_SECRET\"]}", 1)
path.write_text(text, "utf-8")
'
  echo "Created .env with a new local JWT secret."
else
  echo "Keeping the existing .env unchanged."
fi

docker compose up -d postgres qdrant searxng

echo "Waiting for PostgreSQL..."
for attempt in $(seq 1 30); do
  if docker compose exec -T postgres pg_isready -U tf26 -d tf26 >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "PostgreSQL did not become ready in time."
    exit 1
  fi
  sleep 2
done

.venv/bin/alembic upgrade head

echo
echo "Setup complete."
echo "1. Set VLLM_BASE_URL in .env to your OpenAI-compatible model gateway."
echo "2. Run: bash scripts/dev.sh"
echo "3. Open: http://127.0.0.1:3000/tr"
