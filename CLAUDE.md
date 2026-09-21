# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AIJudge: a LiteLLM proxy fronting Gemini, a test chat UI, full request/response
logging to local disk, and a two-tier "Judge" callback: deterministic fast
rules screen every exchange (and can block it outright), and an LLM judge
reviews sessions whose suspicion score has built up. It can block a session's
future access.
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
- `tests/redteam_corpus.py` — attack/benign prompt corpus scored with the
  fast tier's real code (see Commands)
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
set for either process to actually reach Gemini. There is no unit-test suite;
the regression gate is `tests/redteam_corpus.py`. From the repo root,
`.venv\Scripts\python.exe tests\redteam_corpus.py` scores a corpus of
public-technique attack prompts (and benign look-alikes) with the real
`judge_logger._scan_fast`, prints which are blocked / sent to slow review /
missed by the fast tier, and **exits 1 if any case misses its expectation**.
Each case carries one: `block`, `review` (must be caught), `miss-allowed`
(known gap), `benign-pass` (must not trigger review), `fp-allowed` (known
false positive). The expectations pin current behaviour, so when a rule change
legitimately moves a case, update its expectation in the same commit; the
runner prints a `note:` when a known gap is now caught or a known false
positive has gone, as a prompt to tighten it. Run it after changing
`FAST_RULES`. CI (`.github/workflows/redteam.yml`) runs it on every push and PR;
it needs no API key.

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
session), a per-session summary from `data/sessions.json` (suspicion score
and fast-rule latency, keyed by session id) and the slow-review threshold;
the frontend checks locally whether each panel's own id appears in
`blocked_users` and looks up its own entry in `sessions`. `/api/chat` also
reads `sessions.json` right after LiteLLM responds and echoes the request's
fast-rule latency as `fast_check_ms` (the pre-call hook wrote it before the
response came back). The backend still holds no session state of its own.

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

Its main route, `/api/judge-stats`, returns: `judge_rules.FAST_RULES` (with
their raw regex sources, for full transparency about what's actually
enforced) and `judge_rules.SLOW_REVIEW`, `fast_latency` (avg/p50/p95/max of
what the fast tier adds to the request path), the per-session suspicion
table, aggregate totals from `data/stats.json`
(requests, verdict counts, token usage split into chat vs. judge-overhead,
unique session count), a derived `requests_per_second` (see below), the
current blocklist, and up to 50 recent verdicts.

`POST /api/blocklist/reset` overwrites `blocked_users.json` with `[]` and
returns what was cleared. It does not touch `sessions.json`: suspicion scores
and review watermarks are kept, so an unblocked session carries its score. There's no coordination needed with the Judge
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

Two tiers, split by cost and certainty. The hard requirement behind the
split: **an LLM call must never add latency to a chat call.**

- **Fast** — deterministic rules from `judge_rules.FAST_RULES` (regex, plus
  an optional named validator such as Luhn). Input rules run in
  `async_pre_call_hook`, *in the request path*, so a `block` rule rejects
  the current request. A `score` rule adds its `points` to the session's
  suspicion score instead. Output rules run in `_handle_event`, after the
  response has gone back. The hook times its own work
  (`time.perf_counter`) and stores it per session and globally — keep it
  that way. The timed window (`_fast_precheck`) is in-memory apart from one
  thing: `_refresh_blocklist` `stat`s the blocklist file on every call (and
  re-reads it when the mtime changed, e.g. after a dashboard reset), which
  is deliberately inside the window because it is real request-path cost.
  All file *writes* (`sessions.json` via the debounced `_schedule_persist`,
  the blocklist file, exchange records) must stay outside it, otherwise the
  reported "latency added by the fast rules" is a lie. Requests from an
  already-blocked session are rejected in the same hook, record a latency
  sample, but are not logged as exchanges.
- **Slow** — the LLM judge, `_slow_review` → `_llm_judge`. Only runs from
  `_handle_event` (async, after the response) when
  `score - reviewed_score >= judge_rules.SLOW_REVIEW_THRESHOLD`. It reviews
  the session's recent transcript (in memory, `_transcripts`) plus the fast
  rules that fired. `bad` blocks the session; `safe` subtracts the reviewed
  score and resets the watermark; `suspicious` sets the watermark to the
  score reviewed (so it needs another threshold's worth before re-review); a
  call *error* leaves the watermark alone so the next exchange retries.
  Content that fires no rule is verdict "safe" but means *unreviewed* — an
  intentional cost/coverage tradeoff (README "Known limitations"), not
  something to "fix" by sending everything to the LLM.

Per-session state (score, watermark, hits, reviews, latency counters) lives
in `JudgeLogger._sessions`, mirrored to `data/sessions.json`. All access is
synchronous on the event loop, so there is no locking; keep it that way (no
`await` in the middle of a read-modify-write of it). Pre-call results are
handed to the post-call event through `_pending` (per-session FIFO), because
the pre-call and post-call hooks share no other channel. A request rejected
in the pre-call hook never gets a matching post-call event
(`_record_blocked_exchange` records it instead), and `_handle_event`
returns early on a failure event with no pending entry for that reason.

The LLM-as-judge call in `_llm_judge` calls Gemini **directly** via
`litellm.acompletion`, bypassing the local proxy, and tags itself with
`metadata={"aijudge_internal": True}`. `_handle_event` checks that flag and
returns early — this is what stops the Judge from recursively logging and
judging its own judgment calls. Preserve both sides of that guard if you
touch this file.

Rules, weights (`POINTS_MINOR`/`POINTS_MAJOR`) and the review threshold live
in `judge_rules.py`, not `judge_logger.py` — the latter only compiles what
`judge_rules.FAST_RULES` defines, and `judge_ui/app.py` serves that same
data to the dashboard. Editing rules anywhere else would desync the
dashboard from what's enforced. `FAST_RULES` must stay JSON-serialisable
(validators are referenced by name via `VALIDATORS`). All patterns compile
case-insensitive; use an inline `(?-i:...)` group for a case-sensitive token
(see the `DAN` pattern, which must not match the name "Dan").

`_scan_fast` scans more than the raw text: `_text_variants` also derives
Unicode-cleaned text (NFKC, combining marks/zero-width/format/control chars
stripped, Cyrillic/Greek homoglyphs folded), de-spaced letters ("i g n o r e"),
a leetspeak-folded copy (only when a letter/digit-mixed token is present),
a rot13 copy, and up to `B64_MAX_CANDIDATES` decoded base64 blobs. A rule
firing on any variant counts (once per rule). It is pure, in-memory and
bounded (`VARIANT_MAX_CHARS`), so it stays inside the timed window; it
costs roughly 2-3x a single regex pass (sub-ms for normal messages). Variants
are input to regexes only — nothing is logged or stored in decoded form. If
you add a new decoder, keep it bounded and put a matching case in the corpus.

The `canary-leak` rule (`block`, scope `both`) matches `judge_rules.CANARY_TOKEN`,
which the chat backend plants in a system message (`SYSTEM_PROMPT` in
`chatui/backend/app.py`). If the token ever appears in an exchange — even
base64/rot13/zero-width-split — the session is blocked: a near-zero-false-
positive system-prompt leak signal. `AIJUDGE_CANARY` overrides it; otherwise
it is derived from `LITELLM_MASTER_KEY` so the backend and the Judge agree
with no shared file. The rule's real pattern is the secret, so it carries a
`redact_pattern` and `judge_ui/app.py` shows that instead. `judge_rules.py`
now reads env at import (still no I/O); the backend imports it *after*
`load_dotenv()` for that reason.

Any rule can carry `"shadow": True` to trial it on real traffic: it is
matched and recorded but never blocks and adds no points. `_apply_hits`
zeroes its points and tags the session hit `shadow`; the blocker lists in
`_fast_precheck`/`_handle_event` skip it; verdict files list it under
`shadow_rules` (separate from `fast_rules`); `_log_shadow_hits` writes it to
`judge_activity.log` — deliberately post-call/background, never inside the
timed fast window. `/api/judge-stats` returns `shadow_hits` (per-rule counts
over the hits sessions still retain, `MAX_HITS_KEPT` each, so recent rather
than lifetime) and the dashboard marks shadow rules in the rules list.
`tests/redteam_corpus.py` ignores shadow rules, i.e. it scores what is enforced.

The NINO rules (`nino-format`, `nino-verify-intent`) are `block` rules on
purpose: UK National Insurance Number handling is a compliance requirement,
not a judgment call, so it never reaches the LLM — deterministic regex only.
It fires on either (1) an NI-number-shaped string anywhere in the input or
output (a PII handling breach on its own, whatever the intent), or (2) a
request to verify/validate/check a NI number even with no real-looking
number present. It deliberately over-flags — a string with the right shape
(two letters, six digits, one suffix letter) that isn't really a NINO still
blocks. For PII, false positives are the safe failure mode.

### Data (`data/`, gitignored, path overridable via `AIJUDGE_DATA_DIR`)

- `data/logs/<uuid>.json` — every request/response pair, written by the Judge
- `data/verdicts/<uuid>.json` — the Judge's verdict for that pair, read by
  `chatui/backend/app.py`'s `/api/status` and by `judge_ui/app.py`'s
  `/api/judge-stats`
- `data/blocked_users.json` — flat JSON array of blocked session ids, written
  by the Judge (and cleared by the dashboard's reset), read by the Judge's
  pre-call hook, `/api/status`, and `/api/judge-stats`
- `data/judge_activity.log` — every exchange, judge prompt, judge raw
  response, and final verdict, via the `aijudge` logger in
  `judge_logger.py` (also mirrored to the LiteLLM proxy's console)
- `data/sessions.json` — per-session suspicion score, review watermark,
  recent fast-rule hits, last slow reviews, and fast-rule latency (per
  session + a global total/max and the last 200 samples). Written by
  `judge_logger.py` (debounced, temp file + `os.replace`); read by
  `chatui/backend/app.py` (the per-request `fast_check_ms` echoed to the
  browser, and a session summary in `/api/status`) and `judge_ui/app.py`
  (`fast_latency` percentiles and the session suspicion table). Verdict
  files also carry `fast_latency_ms`, `fast_rules`, `points`,
  `session_score`.
- `data/stats.json` — running totals only `judge_logger.py` writes to and
  only `judge_ui/app.py` reads: `total_requests`, `verdict_counts`, token
  usage (`total_prompt_tokens`/`total_completion_tokens`/`total_tokens`,
  plus `judge_overhead_tokens` from the LLM-judge's own calls, tracked
  separately in `_llm_judge`), `unique_sessions` (list, for a count), and
  `recent_timestamps` (rolling window, trimmed to `STATS_WINDOW_SECONDS` on
  every write, that the dashboard turns into requests/sec). Token
  attribution also lives here: `judge_prompt_tokens`/`judge_completion_tokens`/
  `judge_calls`, `judge_paths` (how each exchange was resolved: `fast_block` /
  `fast` / `slow` — only `slow` costs tokens), `token_buckets` (per-minute
  chat vs judge totals, last 60 minutes, drives the dashboard timeline) and
  `session_tokens` (per-session chat/judge totals). Verdict files also carry
  `chat_tokens`, `judge_tokens` and `judge_path`. All updates go
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

All three processes read `blocked_users.json`, `sessions.json` and
`data/verdicts/` independently — they're different Python processes, so `data/` is the only
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
