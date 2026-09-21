#!/usr/bin/env bash
# Runs the LiteLLM proxy on port 4000. Must run from litellm_proxy/ so the
# judge_logger.py callback module resolves.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
. "$root/scripts/load-env.sh"
cd "$root/litellm_proxy"
exec "$root/.venv/bin/litellm" --config config.yaml --port 4000
