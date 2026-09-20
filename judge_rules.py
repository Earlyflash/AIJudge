"""
Shared, side-effect-free rule definitions for AIJudge.

Imported by both the Judge (litellm_proxy/judge_logger.py, which compiles
these into regex objects and runs them) and the Judge Dashboard
(judge_ui/app.py, which only displays them) — this is the single source of
truth for what the rules actually are, so the dashboard can never drift out
of sync with what's really being enforced.

Two tiers:

  FAST — deterministic (regex, plus an optional validator like Luhn), run on
  every exchange with no AI involved. Input rules run in the LiteLLM
  pre-call hook, i.e. in the request path, so they can stop the *current*
  request; the Judge measures and stores how much latency that adds (see
  judge_logger.py). Output rules run after the response has been returned.
  Each rule has one action:
    "block" — the session goes on the blocklist immediately, no AI.
    "score" — add `points` to the session's suspicion score.

  SLOW — the LLM judge. Never run per exchange: when a session's suspicion
  score has climbed SLOW_REVIEW_THRESHOLD points since it was last reviewed,
  the LLM reviews the session as a whole (see SLOW_REVIEW below).

Keep this free of logging setup, file I/O, network calls, or any other
side effect: it must be safe to import from either process at any time.
"""

# Suspicion points. A minor signal is worth noting; a major one is enough on
# its own to trigger a slow review.
POINTS_MINOR = 1
POINTS_MAJOR = 5

# Slow review triggers once (score - score_at_last_review) >= this. `>=`, so
# a single POINTS_MAJOR hit is enough on its own.
SLOW_REVIEW_THRESHOLD = 5


def luhn_valid(candidate: str) -> bool:
    """Luhn checksum over the digits in `candidate` — cuts card-number false
    positives (order numbers, phone numbers) that a bare digit-count regex
    would flag."""
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# Named so FAST_RULES stays plain JSON-serialisable data (the dashboard
# sends it to the browser); the Judge looks the callable up here.
VALIDATORS = {"luhn": luhn_valid}

# UK National Insurance Number shape: two letters (excluding D,F,I,Q,U,V as
# either letter, and O as the second), six digits, one suffix letter A-D.
NINO_FORMAT_REGEX = r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"

NINO_MENTION = r"(?:national insurance(?: number)?|\bNI\s?number\b|\bNINO\b)"
NINO_VERIFY_INTENT_REGEX = (
    rf"(?:verify|validate|check|confirm|is (?:it|this|that) (?:a )?(?:real|valid)|look\s?up).{{0,60}}{NINO_MENTION}"
    rf"|{NINO_MENTION}.{{0,60}}(?:verify|validate|check|confirm|real|valid|look\s?up)"
)

# Evaluated in this order; every matching rule contributes (a block rule
# wins outright, score rules add up). All patterns compile case-insensitive;
# use an inline (?-i:...) group for the rare case-sensitive token.
#
# scope: "input" (the user's message), "output" (the assistant's reply) or
# "both". Injection phrasing is input-only on purpose — an assistant
# *refusing* an "ignore previous instructions" attempt will often quote it,
# and that shouldn't score twice.
FAST_RULES = [
    {
        "id": "nino-format",
        "name": "NI number present",
        "description": (
            "A UK National Insurance number-shaped string in the exchange is a PII "
            "handling breach on its own, whatever the intent. Deliberately over-flags: "
            "anything with the right shape (two letters, six digits, a suffix letter) "
            "blocks, even if it isn't really a NINO."
        ),
        "scope": "both",
        "action": "block",
        "points": 0,
        "pattern": NINO_FORMAT_REGEX,
    },
    {
        "id": "nino-verify-intent",
        "name": "NI number verification request",
        "description": (
            "Asking the model to verify/validate/check a National Insurance number "
            "blocks even when no real-looking number is supplied."
        ),
        "scope": "both",
        "action": "block",
        "points": 0,
        "pattern": NINO_VERIFY_INTENT_REGEX,
    },
    {
        "id": "secret-aws-key",
        "name": "AWS access key",
        "description": "An AWS access key id (AKIA…/ASIA…) pasted into the exchange.",
        "scope": "both",
        "action": "block",
        "points": 0,
        "pattern": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    },
    {
        "id": "secret-api-token",
        "name": "API token",
        "description": "A key/token-shaped string (sk-…, pk-…, api_key-…) pasted into the exchange.",
        "scope": "both",
        "action": "block",
        "points": 0,
        "pattern": r"\b(?:sk|pk|api[_-]?key)[-_][A-Za-z0-9]{16,}\b",
    },
    {
        "id": "card-number",
        "name": "Payment card number",
        "description": "A 13–16 digit number that passes the Luhn checksum.",
        "scope": "both",
        "action": "score",
        "points": POINTS_MAJOR,
        "pattern": r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)",
        "validator": "luhn",
    },
    {
        "id": "ssn-us",
        "name": "US Social Security number",
        "description": "A ddd-dd-dddd shaped number.",
        "scope": "both",
        "action": "score",
        "points": POINTS_MAJOR,
        "pattern": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
    },
    {
        "id": "injection-override",
        "name": "Instruction override",
        "description": "“Ignore/disregard previous instructions” phrasing, or a request to reveal the instructions/prompt/rules.",
        "scope": "input",
        "action": "score",
        "points": POINTS_MAJOR,
        "pattern": (
            r"(?:ignore|disregard) (?:all|previous|above|prior) instructions"
            r"|reveal your (?:instructions|prompt|rules)"
        ),
    },
    {
        "id": "jailbreak",
        "name": "Jailbreak wording",
        "description": "“Jailbreak”, DAN, or “you are now in developer/DAN mode”.",
        "scope": "input",
        "action": "score",
        "points": POINTS_MAJOR,
        "pattern": r"\bjailbreak\b|\b(?-i:DAN)\b|you are now (?:in )?(?:developer|dan) mode",
    },
    {
        "id": "system-prompt-mention",
        "name": "System prompt mention",
        "description": "Mentions “system prompt” — often innocent, so only a minor signal.",
        "scope": "input",
        "action": "score",
        "points": POINTS_MINOR,
        "pattern": r"system prompt",
    },
    {
        "id": "api-key-mention",
        "name": "API key mention",
        "description": "Mentions an API key — often innocent, so only a minor signal.",
        "scope": "input",
        "action": "score",
        "points": POINTS_MINOR,
        "pattern": r"api[_-]?key",
    },
    {
        "id": "persona-request",
        "name": "Persona request",
        "description": "“Pretend you are/to be …” — often innocent role-play, so only a minor signal.",
        "scope": "input",
        "action": "score",
        "points": POINTS_MINOR,
        "pattern": r"pretend (?:you|to) (?:are|be)",
    },
    {
        "id": "email-address",
        "name": "Email address",
        "description": "An email address in the exchange.",
        "scope": "both",
        "action": "score",
        "points": POINTS_MINOR,
        "pattern": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    },
    {
        "id": "phone-number",
        "name": "Phone number",
        "description": "A phone-number-shaped string in the exchange (loose match).",
        "scope": "both",
        "action": "score",
        "points": POINTS_MINOR,
        "pattern": r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)",
    },
]

SLOW_REVIEW = {
    "id": "slow-review",
    "name": "Session review (LLM-as-judge)",
    "threshold": SLOW_REVIEW_THRESHOLD,
    "description": (
        "Runs when a session's suspicion score has climbed by the threshold since it was "
        "last reviewed. The LLM sees the recent transcript plus the fast-rule signals, and "
        "judges the user's intent, not whether the assistant complied — a refused "
        "prompt-injection attempt still scores “bad”. “bad” blocks the session; “safe” "
        "resets its score to 0; “suspicious” keeps the score and waits for another "
        "threshold's worth. Returns “bad” by default if it gets no content back (likely its "
        "own safety filter). Async and off the request path, so it adds no latency; costs tokens."
    ),
}
