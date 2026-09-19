"""
Thin backend: serves the frontend, proxies chat requests to the LiteLLM
proxy on behalf of the browser, and exposes judge activity for the UI.

The browser never talks to LiteLLM directly or holds any key. Session
identity is chosen by the frontend (a random id per chat panel, not a
cookie) and sent explicitly with each request as `session_id` — that's
what lets the UI run several independent chat sessions in parallel from
the same browser. It's forwarded to LiteLLM as the OpenAI `user` field,
which is the id the Judge blocks when it flags an exchange as bad.

The full Judge Dashboard (rules, token/request stats) is a separate
service — see judge_ui/app.py — this backend only needs enough of the
same data to drive its own "Recent Verdicts"/"Blocked Sessions" sidebar.
"""

import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

APP_DIR = Path(__file__).resolve().parent.parent  # chatui/
REPO_ROOT = APP_DIR.parent
FRONTEND_DIR = APP_DIR / "frontend"

# Same anchoring logic as litellm_proxy/judge_logger.py — a relative
# AIJUDGE_DATA_DIR must resolve against the repo root, not whatever cwd
# this process happens to be started from.
_data_dir_env = os.environ.get("AIJUDGE_DATA_DIR")
DATA_DIR = ((REPO_ROOT / _data_dir_env) if _data_dir_env else (REPO_ROOT / "data")).resolve()
VERDICTS_DIR = DATA_DIR / "verdicts"
BLOCKLIST_PATH = DATA_DIR / "blocked_users.json"

LITELLM_BASE = os.environ.get("AIJUDGE_LITELLM_BASE", "http://localhost:4000")
LITELLM_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
CHAT_MODEL = os.environ.get("AIJUDGE_CHAT_MODEL", "gemini-flash")

app = FastAPI(title="AIJudge")


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat")
async def chat(req: ChatRequest):
    session_id = req.session_id.strip()
    message = req.message.strip()
    if not session_id:
        return JSONResponse(status_code=400, content={"error": "session_id is required"})
    if not message:
        return JSONResponse(status_code=400, content={"error": "message is required"})

    payload = {
        "model": CHAT_MODEL,
        "messages": [{"role": "user", "content": message}],
        "user": session_id,
    }
    headers = {"Authorization": f"Bearer {LITELLM_KEY}"}

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{LITELLM_BASE}/chat/completions", json=payload, headers=headers)
    except httpx.RequestError as e:
        return JSONResponse(status_code=502, content={"error": f"Could not reach LiteLLM proxy: {e}"})
    latency_ms = round((time.monotonic() - start) * 1000)

    if resp.status_code >= 400:
        return JSONResponse(
            status_code=resp.status_code,
            content={"error": resp.text, "latency_ms": latency_ms},
        )

    data = resp.json()
    reply = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return {
        "reply": reply,
        "session_id": session_id,
        "latency_ms": latency_ms,
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens") or 0,
        },
    }


@app.get("/api/status")
async def status():
    blocked_users = []
    if BLOCKLIST_PATH.exists():
        blocked_users = json.loads(BLOCKLIST_PATH.read_text())

    recent_verdicts = []
    if VERDICTS_DIR.exists():
        files = sorted(VERDICTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        for f in files:
            try:
                recent_verdicts.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                continue

    return {
        "blocked_users": blocked_users,
        "recent_verdicts": recent_verdicts,
    }


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
