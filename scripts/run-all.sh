#!/usr/bin/env bash
# Demo convenience: starts the LiteLLM proxy, chat UI and Judge Dashboard
# together in one terminal. Ctrl-C stops all three. Output is interleaved
# with a prefix per service; use the individual run-*.sh scripts (one
# terminal each) if you want to read a single service's output.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
pids=()

stop() {
    trap - INT TERM EXIT
    echo; echo "Stopping..."
    for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done
    wait 2>/dev/null
    exit 0
}
trap stop INT TERM EXIT

start() {  # start <label> <script>
    ( "$root/scripts/$2" 2>&1 | sed -u "s/^/[$1] /" ) &
    pids+=($!)
}

start litellm run-litellm.sh
sleep 3   # let the proxy start first; the chat backend only needs it on the first request
start chat   run-backend.sh
start judge  run-judge-ui.sh

echo "Chat UI:         http://localhost:8000"
echo "Judge Dashboard: http://localhost:8010"
echo "LiteLLM proxy:   http://localhost:4000   (Ctrl-C to stop everything)"
wait
