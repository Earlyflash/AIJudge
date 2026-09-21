#!/usr/bin/env bash
# Generates simulated chat sessions against the running LiteLLM proxy so the
# Judge Dashboard can be watched at volume. Arguments pass straight through:
#   scripts/load-test.sh --sessions 100 --bad-pct 30
#   scripts/load-test.sh --help
# Every request is a real Gemini call, so mind the cost and rate limits.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
exec "$root/.venv/bin/python" tests/load_sessions.py "$@"
