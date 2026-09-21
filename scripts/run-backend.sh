#!/usr/bin/env bash
# Runs the chat test UI's FastAPI backend + static frontend on port 8000.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
. "$root/scripts/load-env.sh"
cd "$root"
exec "$root/.venv/bin/uvicorn" chatui.backend.app:app --reload --port 8000
