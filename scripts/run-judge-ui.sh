#!/usr/bin/env bash
# Runs the standalone Judge Dashboard on port 8010. Independent of the chat
# test UI (run-backend.sh) - reads data/ directly off disk.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
. "$root/scripts/load-env.sh"
cd "$root"
exec "$root/.venv/bin/uvicorn" judge_ui.app:app --reload --port 8010
