# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AIJudge: a LiteLLM proxy fronting Gemini, a test chat UI, full request/response
logging to local disk, and an async "Judge" callback that reviews every
exchange for suspicious/bad content and can block a session's future access.
Three independently-run processes: LiteLLM proxy, FastAPI backend (also
serves the static frontend), and the browser.

## Commands

```powershell
.\scripts\setup.ps1         # one-time: creates .venv, installs requirements.txt, copies .env.example -> .env
.\scripts\run-litellm.ps1   # LiteLLM proxy on :4000 (must run with cwd=litellm_proxy/, script handles this)
.\scripts\run-backend.ps1   # FastAPI backend + static frontend on :8000, --reload enabled
```

Both run scripts dot-source `scripts/load-env.ps1` to load `.env` into the
process environment before launching — there is no other env-loading path
for the LiteLLM proxy, so if you run `litellm` manually, load `.env` first.

`.env` (copied from `.env.example`, gitignored) must have `GEMINI_API_KEY`
set for either process to actually reach Gemini. No test suite exists yet.

## Architecture

```
Browser (frontend/, static, no build step, two independent chat panels)
   |  POST /api/chat  { session_id, message }  (never sees any key)
   v
Backend (backend/app.py, FastAPI, :8000)
   |  POST /chat/completions    (Authorization: Bearer LITELLM_MASTER_KEY)
   v
LiteLLM proxy (litellm_proxy/, :4000)
   |  gemini/gemini-3.6-flash
   v
Gemini API
```

The backend is the only caller of LiteLLM — the frontend never talks to it
directly. Session identity is **not** a cookie: each chat panel
(`frontend/app.js`'s `ChatPanel` class) generates its own `crypto.randomUUID()`
client-side and sends it explicitly as `session_id` in every `/api/chat`
call. This is what lets the UI run two (or more) independent sessions in
parallel from one browser tab — a cookie is one-per-domain and can't do
that. The backend has no server-side session state at all; it just forwards
whatever `session_id` it's given to LiteLLM as the OpenAI `user` field,
which is the identity the Judge blocks. `/api/status` returns the global
blocklist/verdict lists (not scoped to a session) and the frontend checks
locally whether each panel's own id appears in `blocked_users`.

### The Judge (`litellm_proxy/judge_logger.py`)

Registered as a LiteLLM proxy callback via `litellm_settings.callbacks` in
`litellm_proxy/config.yaml` (`judge_logger.judge_callback`). Because of that
config reference, **the LiteLLM proxy must be started with
`litellm_proxy/` as its working directory** — the run script already does
this via `Push-Location`; don't move `config.yaml` and `judge_logger.py`
apart or break that cwd assumption without updating the callback path.

It has two responsibilities on deliberately different timelines — this
split is the load-bearing design decision of the whole project, driven by
a hard requirement that judging must never add latency to a chat call:

- **Enforcement** — `async_pre_call_hook`, runs synchronously in the
  request path before every call. Kept to a single in-memory set lookup
  against the blocklist on purpose. It can only reject a call based on a
  verdict from a *previous* exchange, never the current one.
- **Judging** — `async_log_success_event` / `async_log_failure_event`,
  fired via `asyncio.create_task` *after* the response has already gone
  back to the caller. Writes the full exchange to `data/logs/`, runs a
  cheap regex filter (`SUSPICIOUS_PATTERNS`) first, and only escalates
  ambiguous matches to an LLM-as-judge Gemini call (`_llm_judge`). A
  "bad" verdict appends the user id to `data/blocked_users.json`, which
  enforcement reads (with an mtime check to pick up out-of-process writes).

The LLM-as-judge call in `_llm_judge` calls Gemini **directly** via
`litellm.acompletion`, bypassing the local proxy, and tags itself with
`metadata={"aijudge_internal": True}`. `_handle_event` checks that flag and
returns early — this is what stops the Judge from recursively logging and
judging its own judgment calls. Preserve both sides of that guard if you
touch this file.

If you change `SUSPICIOUS_PATTERNS` or the escalation logic: content that
matches no pattern is marked "safe" without ever reaching the LLM judge —
this is an intentional cost/coverage tradeoff (see README "Known
limitations"), not an oversight to "fix" by removing the fast path.

### Data (`data/`, gitignored, path overridable via `AIJUDGE_DATA_DIR`)

- `data/logs/<uuid>.json` — every request/response pair, written by the Judge
- `data/verdicts/<uuid>.json` — the Judge's verdict for that pair, read by
  `backend/app.py`'s `/api/status` for the UI's "Recent Verdicts" panel
- `data/blocked_users.json` — flat JSON array of blocked session ids, written
  by the Judge, read by both the Judge's own enforcement hook and
  `/api/status`'s "Blocked Sessions" panel
- `data/judge_activity.log` — every exchange, judge prompt, judge raw
  response, and final verdict, via the `aijudge` logger in
  `judge_logger.py` (also mirrored to the LiteLLM proxy's console)

Both `judge_logger.py` and `backend/app.py` anchor a relative
`AIJUDGE_DATA_DIR` to the repo root explicitly (`Path(__file__).resolve()
.parent.parent`), not to the process's cwd — the two processes are started
from different working directories (`litellm_proxy/` vs. repo root), so
resolving a relative path against cwd previously made them read/write two
different `data/` folders without any error. If you touch this path logic,
keep both files' resolution identical.

Both the Judge process (LiteLLM proxy) and the backend process read
`blocked_users.json` and `data/verdicts/` independently — they're different
Python processes, so this file is the only shared state between them. There
is no locking; writes are whole-file rewrites of a small JSON array, which is
fine at this scale but wouldn't survive concurrent writers at higher volume.

### Model name plumbing

Three separate env vars name models for three different purposes — don't
collapse them, they mean different things:

- `AIJUDGE_CHAT_MODEL` (backend) — the LiteLLM `model_name` (from
  `litellm_proxy/config.yaml`'s `model_list`) used for user-facing chat.
  Must match a `model_name` entry in that config.
- `AIJUDGE_JUDGE_MODEL` (judge_logger.py) — a raw provider model string
  (e.g. `gemini/gemini-3.6-flash`) for the Judge's direct-to-Gemini calls.
  Not a LiteLLM proxy `model_name` — it's passed straight to
  `litellm.acompletion`, which is why it needs the `gemini/` prefix.
- `litellm_proxy/config.yaml`'s `model_list[].model_name` (`gemini-flash`)
  — what the proxy exposes to clients; `AIJUDGE_CHAT_MODEL` must match this.
