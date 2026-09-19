# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AIJudge: a LiteLLM proxy fronting Gemini, a test chat UI, full request/response
logging to local disk, and an async "Judge" callback that reviews every
exchange for suspicious/bad content and can block a session's future access.
There's also a standalone Judge Dashboard, deliberately a separate service
from the chat UI (see "Judge Dashboard" below).

Four independently-run processes: LiteLLM proxy (runs the Judge), the chat
UI's FastAPI backend + static frontend, the standalone Judge Dashboard's
FastAPI backend + static frontend, and the browser.

Top-level layout:
- `litellm_proxy/` — the proxy config and the Judge itself
- `chatui/backend/`, `chatui/frontend/` — the chat test UI
- `judge_ui/` (`app.py` + `frontend/`) — the standalone Judge Dashboard
- `judge_rules.py` (repo root) — rule definitions shared by the Judge and
  the dashboard, so they can never drift apart
- `data/` (gitignored) — the only thing all three processes share

## Commands

```powershell
.\scripts\setup.ps1         # one-time: creates .venv, installs requirements.txt, copies .env.example -> .env
.\scripts\run-litellm.ps1   # LiteLLM proxy on :4000 (must run with cwd=litellm_proxy/, script handles this)
.\scripts\run-backend.ps1   # Chat UI: FastAPI backend + static frontend on :8000, --reload enabled
.\scripts\run-judge-ui.ps1  # Judge Dashboard: FastAPI backend + static frontend on :8010, --reload enabled
```

Both run scripts dot-source `scripts/load-env.ps1` to load `.env` into the
process environment before launching — there is no other env-loading path
for the LiteLLM proxy, so if you run `litellm` manually, load `.env` first.

`.env` (copied from `.env.example`, gitignored) must have `GEMINI_API_KEY`
set for either process to actually reach Gemini. No test suite exists yet.

## Architecture

```
Browser (chatui/frontend/, static, no build step, two independent chat panels)
   |  POST /api/chat  { session_id, message }  (never sees any key)
   v
chatui/backend/app.py (FastAPI, :8000)
   |  POST /chat/completions    (Authorization: Bearer LITELLM_MASTER_KEY)
   v
LiteLLM proxy (litellm_proxy/, :4000)
   |  gemini/gemini-3.6-flash
   v
Gemini API
```

`chatui/backend` is the only caller of LiteLLM — the frontend never talks
to it directly. Session identity is **not** a cookie: each chat panel
(`chatui/frontend/app.js`'s `ChatPanel` class) generates its own
`crypto.randomUUID()` client-side and sends it explicitly as `session_id`
in every `/api/chat` call. This is what lets the UI run two (or more)
independent sessions in parallel from one browser tab — a cookie is
one-per-domain and can't do that. The backend has no server-side session
state at all; it just forwards whatever `session_id` it's given to LiteLLM
as the OpenAI `user` field, which is the identity the Judge blocks.
`/api/status` returns the global blocklist/verdict lists (not scoped to a
session) and the frontend checks locally whether each panel's own id
appears in `blocked_users`.

## Judge Dashboard (`judge_ui/`)

Deliberately a **separate service** from `chatui/` — its own FastAPI app
(`judge_ui/app.py`, port `:8010`) and its own static frontend
(`judge_ui/frontend/`). It has zero dependency on `chatui/backend` and vice
versa: both independently read `data/` off disk and both independently
import `judge_rules.py` from the repo root. This was an explicit choice
(the first pass bolted a `/api/judge-stats` route onto `chatui/backend`;
it was pulled back out) — the dashboard is meant to work as a standalone
admin/compliance view of what the Judge is doing, usable with or without
the chat test UI running at all.

Its main route, `/api/judge-stats`, returns: `judge_rules.RULES_SUMMARY`
plus the raw regex sources (for full transparency about what's actually
enforced — see `judge_rules.py`), aggregate totals from `data/stats.json`
(requests, verdict counts, token usage split into chat vs. judge-overhead,
unique session count), a derived `requests_per_second` (see below), the
current blocklist, and up to 50 recent verdicts.

`POST /api/blocklist/reset` overwrites `blocked_users.json` with `[]` and
returns what was cleared. There's no coordination needed with the Judge
process beyond that write: `_refresh_blocklist` in `judge_logger.py`
compares the file's mtime on every enforcement check and re-reads it if
it's changed, so the reset takes effect on that process's very next call
without a restart. The frontend confirms before calling it (unblocking
everyone is a real, if easily-undone-by-testing-again, action) and disables
the button when the blocklist is already empty.

`requests_per_second` is derived from `stats["recent_timestamps"]`, a
rolling list of epoch-second floats the Judge appends to on every request
and trims to `STATS_WINDOW_SECONDS` (300s, in `judge_logger.py`) on every
write. The dashboard counts how many of those fall within its own
`RPS_WINDOW_SECONDS` (60s, in `judge_ui/app.py`) and divides — cheap to
compute, no need to scan `data/logs/`. If you change one window, keep the
dashboard's `RPS_WINDOW_SECONDS` <= the Judge's `STATS_WINDOW_SECONDS`, or
older timestamps will already be gone before the dashboard can count them.

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

If you change the pattern lists, edit them in `judge_rules.py`, not
`judge_logger.py` — the latter now just compiles what `judge_rules.py`
defines (`SUSPICIOUS_PATTERNS = [re.compile(p, re.I) for p in
judge_rules.SUSPICIOUS_PATTERNS_RAW]`), and `judge_ui/app.py` reads that
same module to display them on the dashboard. Editing a compiled pattern
in `judge_logger.py` directly would desync it from what the dashboard
shows. Content that matches no pattern is marked "safe" without ever
reaching the LLM judge — an intentional cost/coverage tradeoff (see README
"Known limitations"), not an oversight to "fix" by removing the fast path.

`_nino_check` (checked before `_rule_check`, patterns from
`judge_rules.py`'s `NINO_FORMAT_REGEX` / `NINO_VERIFY_INTENT_REGEX`) is a
separate, harder rule: UK National Insurance Number handling is a
compliance requirement, not a judgment call, so it never reaches the LLM
judge at all — deterministic regex only. It fires "bad" on either (1) an
actual NI-number-shaped string anywhere in the input or output (treated as
a PII handling breach on its own, regardless of surrounding context or
intent), or (2) a request to verify/validate/check a NI number even with
no real-looking number present. This deliberately over-flags — a string
that merely has the right shape (two letters, six digits, one suffix
letter) but isn't really a NINO (e.g. some other reference code) still
gets blocked. That's intentional: for PII, false positives are the safe
failure mode here.

### Data (`data/`, gitignored, path overridable via `AIJUDGE_DATA_DIR`)

- `data/logs/<uuid>.json` — every request/response pair, written by the Judge
- `data/verdicts/<uuid>.json` — the Judge's verdict for that pair, read by
  `chatui/backend/app.py`'s `/api/status` and by `judge_ui/app.py`'s
  `/api/judge-stats`
- `data/blocked_users.json` — flat JSON array of blocked session ids, written
  by the Judge, read by the Judge's own enforcement hook, `/api/status`, and
  `/api/judge-stats`
- `data/judge_activity.log` — every exchange, judge prompt, judge raw
  response, and final verdict, via the `aijudge` logger in
  `judge_logger.py` (also mirrored to the LiteLLM proxy's console)
- `data/stats.json` — running totals only `judge_logger.py` writes to and
  only `judge_ui/app.py` reads: `total_requests`, `verdict_counts`, token
  usage (`total_prompt_tokens`/`total_completion_tokens`/`total_tokens`,
  plus `judge_overhead_tokens` from the LLM-judge's own calls, tracked
  separately in `_llm_judge`), `unique_sessions` (list, for a count), and
  `recent_timestamps` (rolling window, trimmed to `STATS_WINDOW_SECONDS` on
  every write, that the dashboard turns into requests/sec). All updates go
  through `_update_stats`'s read-modify-write-whole-file pattern — safe
  under asyncio because there's no `await` between the read and the write
  in any caller, so no other task can interleave.

`judge_logger.py`, `chatui/backend/app.py`, and `judge_ui/app.py` each
anchor a relative `AIJUDGE_DATA_DIR` to the repo root explicitly, computed
from `__file__` rather than the process's cwd — necessary because these
three processes are started from three different working directories
(`litellm_proxy/`, repo root, and repo root again but one directory level
shallower than `chatui/backend/app.py`, since `judge_ui/app.py` sits
directly in `judge_ui/` with no `backend/` subfolder). Concretely:
`judge_logger.py` and `chatui/backend/app.py` both use
`Path(__file__).resolve().parent.parent` to reach the repo root;
`judge_ui/app.py` uses `Path(__file__).resolve().parent` (one less
`.parent`, because it has one less directory level to climb). If you
restructure any of these three, recompute this per file — don't copy the
`.parent` chain from one to another without checking its actual depth.

All three processes read `blocked_users.json` and `data/verdicts/`
independently — they're different Python processes, so `data/` is the only
shared state between them. There is no locking; writes are whole-file
rewrites of small JSON structures, which is fine at this scale but
wouldn't survive concurrent writers at higher volume.

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
