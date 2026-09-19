"""
Shared, side-effect-free rule definitions for AIJudge.

Imported by both the Judge (litellm_proxy/judge_logger.py, which compiles
these into regex objects and runs them) and the backend (backend/app.py,
which only displays them on the Judge Dashboard) — this is the single
source of truth for what the rules actually are, so the dashboard can never
drift out of sync with what's really being enforced.

Keep this free of logging setup, file I/O, network calls, or any other
side effect: it must be safe to import from either process at any time.
"""

# UK National Insurance Number shape: two letters (excluding D,F,I,Q,U,V as
# either letter, and O as the second), six digits, one suffix letter A-D.
NINO_FORMAT_REGEX = r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"

NINO_MENTION = r"(?:national insurance(?: number)?|\bNI\s?number\b|\bNINO\b)"
NINO_VERIFY_INTENT_REGEX = (
    rf"(?:verify|validate|check|confirm|is (?:it|this|that) (?:a )?(?:real|valid)|look\s?up).{{0,60}}{NINO_MENTION}"
    rf"|{NINO_MENTION}.{{0,60}}(?:verify|validate|check|confirm|real|valid|look\s?up)"
)

SUSPICIOUS_PATTERNS_RAW = [
    r"ignore (all|previous|above|prior) instructions",
    r"disregard (all|previous|above|prior) instructions",
    r"system prompt",
    r"api[_-]?key",
    r"reveal your (instructions|prompt|rules)",
    r"\bjailbreak\b",
    r"\bDAN\b",
    r"you are now (in )?(developer|dan) mode",
    r"pretend (you|to) (are|be)",
]

# Human-readable summary for the Judge Dashboard — kept in the order the
# Judge actually evaluates them (see judge_logger.py's _handle_event).
RULES_SUMMARY = [
    {
        "id": "nino-pii",
        "name": "National Insurance Number (PII)",
        "verdict": "bad",
        "mode": "deterministic — no LLM call",
        "description": (
            "Blocks immediately if a UK NI-number-shaped string appears anywhere in "
            "the exchange (a PII handling breach on its own), or if the exchange asks "
            "to verify/validate/check one, even with no real number present."
        ),
    },
    {
        "id": "rule-filter",
        "name": "Suspicious pattern filter",
        "verdict": "escalates to LLM judge on match, else safe",
        "mode": "regex — cheap, no LLM call unless it matches",
        "description": (
            "A small pattern list (prompt-injection phrasing, “system prompt”, "
            "“api key”, jailbreak wording, etc.). A match doesn't mean bad on its "
            "own — it escalates to the LLM judge. No match is marked safe without "
            "spending an LLM call."
        ),
    },
    {
        "id": "llm-judge",
        "name": "LLM-as-judge (Gemini, direct)",
        "verdict": "safe / suspicious / bad",
        "mode": "probabilistic — async, off the request's critical path",
        "description": (
            "Only reached for ambiguous cases. Scores the user's intent, not whether "
            "the assistant complied — a prompt-injection attempt the model successfully "
            "refused still scores “bad”, not “suspicious”. Returns “bad” "
            "by default if it gets no content back (likely its own safety filter)."
        ),
    },
]
