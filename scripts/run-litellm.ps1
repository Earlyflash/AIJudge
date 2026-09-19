# Runs the LiteLLM proxy on port 4000. Must run from litellm_proxy/ so the
# judge_logger.py callback module resolves.
$root = Join-Path $PSScriptRoot ".."
. (Join-Path $PSScriptRoot "load-env.ps1")
& (Join-Path $root ".venv\Scripts\Activate.ps1")
Push-Location (Join-Path $root "litellm_proxy")
try {
    litellm --config config.yaml --port 4000
} finally {
    Pop-Location
}
