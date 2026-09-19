"""
Judge Dashboard: a standalone service, independent of the chat test UI.

Reads data/ directly off disk (stats.json, blocked_users.json, verdicts/)
and the shared judge_rules.py for what the rules actually are — it has no
dependency on chatui/backend at all, and chatui/backend has none on this.
An admin/compliance user can run this on its own to see what the Judge is
doing without needing the chat testing tool running at all.
"""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

load_dotenv()

APP_DIR = Path(__file__).resolve().parent  # judge_ui/
REPO_ROOT = APP_DIR.parent
FRONTEND_DIR = APP_DIR / "frontend"

sys.path.insert(0, str(REPO_ROOT))
import judge_rules  # noqa: E402 — shared, side-effect-free rule definitions

# Same anchoring logic as litellm_proxy/judge_logger.py and
# chatui/backend/app.py — a relative AIJUDGE_DATA_DIR must resolve against
# the repo root, not whatever cwd this process happens to be started from.
_data_dir_env = os.environ.get("AIJUDGE_DATA_DIR")
DATA_DIR = ((REPO_ROOT / _data_dir_env) if _data_dir_env else (REPO_ROOT / "data")).resolve()
VERDICTS_DIR = DATA_DIR / "verdicts"
BLOCKLIST_PATH = DATA_DIR / "blocked_users.json"
STATS_PATH = DATA_DIR / "stats.json"

# Window (seconds) over which requests/sec is computed — must match (or be
# shorter than) STATS_WINDOW_SECONDS in litellm_proxy/judge_logger.py,
# which is how long a timestamp survives in stats["recent_timestamps"].
RPS_WINDOW_SECONDS = 60

app = FastAPI(title="AIJudge — Judge Dashboard")


@app.get("/api/judge-stats")
async def judge_stats():
    blocked_users = []
    if BLOCKLIST_PATH.exists():
        blocked_users = json.loads(BLOCKLIST_PATH.read_text())

    stats = {}
    if STATS_PATH.exists():
        try:
            stats = json.loads(STATS_PATH.read_text())
        except json.JSONDecodeError:
            stats = {}

    recent_timestamps = stats.get("recent_timestamps", [])
    now = time.time()
    requests_last_window = sum(1 for t in recent_timestamps if now - t <= RPS_WINDOW_SECONDS)

    recent_verdicts = []
    if VERDICTS_DIR.exists():
        files = sorted(VERDICTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]
        for f in files:
            try:
                recent_verdicts.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                continue

    return {
        "rules": judge_rules.RULES_SUMMARY,
        "suspicious_patterns": judge_rules.SUSPICIOUS_PATTERNS_RAW,
        "nino_format_pattern": judge_rules.NINO_FORMAT_REGEX,
        "nino_intent_pattern": judge_rules.NINO_VERIFY_INTENT_REGEX,
        "totals": {
            "total_requests": stats.get("total_requests", 0),
            "verdict_counts": stats.get("verdict_counts", {"safe": 0, "suspicious": 0, "bad": 0}),
            "total_prompt_tokens": stats.get("total_prompt_tokens", 0),
            "total_completion_tokens": stats.get("total_completion_tokens", 0),
            "total_tokens": stats.get("total_tokens", 0),
            "judge_overhead_tokens": stats.get("judge_overhead_tokens", 0),
            "unique_sessions": len(stats.get("unique_sessions", [])),
            "blocked_sessions": len(blocked_users),
        },
        "requests_per_second": round(requests_last_window / RPS_WINDOW_SECONDS, 3),
        "requests_last_window": requests_last_window,
        "window_seconds": RPS_WINDOW_SECONDS,
        "blocked_users": blocked_users,
        "recent_verdicts": recent_verdicts,
    }


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
