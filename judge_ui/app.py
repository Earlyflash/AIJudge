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

# How many one-minute token buckets the dashboard's timeline shows. Must be
# <= TOKEN_BUCKET_KEEP in litellm_proxy/judge_logger.py.
TIMELINE_MINUTES = 30

app = FastAPI(title="AIJudge — Judge Dashboard")


@app.post("/api/blocklist/reset")
async def reset_blocklist():
    cleared = []
    if BLOCKLIST_PATH.exists():
        try:
            cleared = json.loads(BLOCKLIST_PATH.read_text())
        except json.JSONDecodeError:
            cleared = []
    BLOCKLIST_PATH.write_text("[]")
    # The Judge (a different process) refreshes its in-memory blocklist
    # from this file's mtime on the next call, so no other coordination
    # is needed for enforcement to pick this up.
    return {"cleared_count": len(cleared), "cleared": cleared}


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

    # Per-minute chat vs judge token spend for the last TIMELINE_MINUTES,
    # zero-filled so the chart has a continuous x axis.
    buckets = stats.get("token_buckets", {})
    this_minute = int(now // 60) * 60
    token_timeline = []
    for i in range(TIMELINE_MINUTES - 1, -1, -1):
        t = this_minute - i * 60
        b = buckets.get(str(t), {})
        token_timeline.append({"t": t, "chat": b.get("chat", 0), "judge": b.get("judge", 0)})

    session_tokens = stats.get("session_tokens", {})
    top_sessions = sorted(
        ({"session_id": sid, **v} for sid, v in session_tokens.items()),
        key=lambda s: s.get("chat", 0) + s.get("judge", 0),
        reverse=True,
    )[:8]

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
            "judge_prompt_tokens": stats.get("judge_prompt_tokens", 0),
            "judge_completion_tokens": stats.get("judge_completion_tokens", 0),
            "judge_calls": stats.get("judge_calls", 0),
            "unique_sessions": len(stats.get("unique_sessions", [])),
            "blocked_sessions": len(blocked_users),
        },
        "requests_per_second": round(requests_last_window / RPS_WINDOW_SECONDS, 3),
        "requests_last_window": requests_last_window,
        "window_seconds": RPS_WINDOW_SECONDS,
        "judge_paths": stats.get("judge_paths", {"nino": 0, "rule": 0, "llm": 0}),
        "token_timeline": token_timeline,
        "top_sessions": top_sessions,
        "blocked_users": blocked_users,
        "recent_verdicts": recent_verdicts,
    }


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
