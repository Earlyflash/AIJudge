# Generates simulated chat sessions against the running LiteLLM proxy so the
# Judge Dashboard can be watched at volume. Arguments pass straight through:
#   .\scripts\load-test.ps1 --sessions 100 --bad-pct 30
# Every request is a real Gemini call, so mind the cost and rate limits.
$root = Join-Path $PSScriptRoot ".."
Push-Location $root
try {
    & (Join-Path $root ".venv\Scripts\python.exe") tests\load_sessions.py @args
} finally {
    Pop-Location
}
