# Runs the standalone Judge Dashboard on port 8010. Independent of the
# chat test UI (run-backend.ps1) - reads data/ directly off disk.
$root = Join-Path $PSScriptRoot ".."
. (Join-Path $PSScriptRoot "load-env.ps1")
& (Join-Path $root ".venv\Scripts\Activate.ps1")
Push-Location $root
try {
    uvicorn judge_ui.app:app --reload --port 8010
} finally {
    Pop-Location
}
