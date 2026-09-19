# Runs the chat test UI's FastAPI backend + static frontend on port 8000.
$root = Join-Path $PSScriptRoot ".."
. (Join-Path $PSScriptRoot "load-env.ps1")
& (Join-Path $root ".venv\Scripts\Activate.ps1")
Push-Location $root
try {
    uvicorn chatui.backend.app:app --reload --port 8000
} finally {
    Pop-Location
}
