# Ideas for extending AIJudge

Backlog of possible improvements, roughly grouped. Nothing here is committed
work. Effort is a rough guess: **S** = under a day, **M** = a few days,
**L** = a week or more.

Already built (so not listed below): the red-team corpus as a CI regression
gate, input normalisation (Unicode, homoglyphs, spacing, leetspeak, rot13,
base64), canary tokens, shadow-mode rules, the SQLite state store, the
per-session drill-down, macOS/Linux scripts and the load generator.

## Detection quality

| Idea | Why | Effort |
|---|---|---|
| **Multi-turn pattern rules.** Score sequences (probe, get refused, rephrase, escalate), plus session features such as block-retry rate and near-duplicate prompts. | The low-and-slow probe is the biggest known fast-tier gap in the corpus, and these signals cost almost nothing per call. | M |
| **Cheap semantic tier.** Embed each input and compare with a small bank of known attacks. Run it off the request path, or measure it inside the latency window. | Catches paraphrased jailbreaks and other languages that regex can't. | M |
| **Score decay.** Let suspicion fall over time so long-lived benign sessions aren't penalised. | Today the score only rises, or drops after a `safe` review. | S |
| **More decoders.** Hex, URL-encoding, reversed text, Morse, and a second layer of encoding. | Each is a cheap addition, and each needs a corpus case. | S |
| **Output-side blocking before delivery.** Today output rules run after the reply is sent, so they only affect later turns. Stream-scan or buffer for high-risk rules. | Closes the "leak already delivered" gap, at the cost of some latency. | L |
| **Prompt-injection in retrieved content.** Scan tool results and documents, not only user turns. | Indirect injection is the realistic attack once the assistant has tools or RAG. | M |

## Evaluation

- **Judge accuracy benchmark (M).** Run labelled sessions through the slow
  tier for precision and recall, compare judge models and prompts, and track
  token cost per catch. `tests/load_sessions.py --out` already produces
  labelled results to build on.
- **Rule-hit analytics (S).** Which rules fire most, which produce false
  positives (fired, then a `safe` review), and which never fire. The data is
  in the verdict files.
- **Lifetime shadow-rule counters (S).** Shadow hit counts only cover the hits
  each session still retains. A counter in the store would make shadow-mode
  trials a proper promote-or-drop decision.
- **Load test in CI (S).** Run `load_sessions.py` against a stub proxy to
  catch regressions in the generator, and run `tests/store_multiprocess.py`
  in CI as well (it isn't in the workflow yet).
- **End-to-end canary test (S).** Prove a real model reply containing the
  canary gets blocked. Only the input side has been exercised.

## Operations and dashboard

- **Selective unblock, block reasons and expiry (M).** Replace the
  reset-everyone button with per-session unblock, store why each session was
  blocked, and allow temporary blocks.
- **Rule editing and hot reload (M).** Edit rules and shadow/enforce flags
  from the dashboard, with reload, instead of editing `judge_rules.py`.
- **Alerting (S).** Webhook (Teams/Slack) on a block, a spike in requests per
  second, or a run of `bad` verdicts.
- **Human review queue (M).** Route `suspicious` verdicts to an analyst and
  store their decision as labelled data for the benchmark above.
- **Session drill-down at scale (S).** Index verdicts by session instead of
  scanning files, and keep the judge prompt and raw reply in the store
  instead of parsing the tail of `judge_activity.log` (older reviews currently
  show "not found").
- **Search and filters (S).** Filter the session table by blocked, review due,
  or rule, and search transcripts.
- **Dashboard auth (S).** The dashboard has no login and can reset the
  blocklist, and it displays user prompts. Fine locally, not for a shared
  demo server.

## Architecture and scale

- **Stronger identity (L).** A client-chosen `session_id` is trivially
  rotated to escape a block. Also key on the authenticated user, IP or
  API key, with a linked-identity score.
- **Retention and PII handling (M).** Logs hold raw prompts. Redact matched
  PII (especially NINO hits) before writing, and add TTL cleanup of `logs/`
  and `verdicts/`.
- **Per-tenant policies (M).** Different rule sets and thresholds per LiteLLM
  virtual key.
- **Database beyond one machine (L).** SQLite/WAL needs a local disk and one
  host. Multiple proxy hosts would need Postgres or Redis behind `judge_store.py`.
- **Containerise for demos (S).** A `docker-compose.yml` for the three
  services removes the Python-version and venv setup on the Macs.
- **Python version matrix (S).** CI runs one Python version. Check which
  versions the pinned LiteLLM supports, since new macOS Homebrew Pythons move
  quickly.

## Ordering suggestion

Cheapest wins first: rule-hit analytics, lifetime shadow counters, the two CI
additions, score decay, and the dashboard drill-down index. Then the biggest
detection gap (multi-turn rules), then the accuracy benchmark, so later
detection work can be measured instead of guessed.
