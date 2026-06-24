# Start the backend using the VENV python (never the global one).
# Usage:  .\run.ps1            -> http://127.0.0.1:8000  (with --reload)
#         .\run.ps1 -Port 8001 -NoReload
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPy = Join-Path $here "venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Host "[ERROR] venv python not found at $venvPy" -ForegroundColor Red
    Write-Host "        Create it first:  python -m venv venv ; .\venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

$reload = if ($NoReload) { "" } else { "--reload" }
Write-Host "[run] Starting backend with venv python on ${BindHost}:${Port}" -ForegroundColor Cyan
& $venvPy -m uvicorn app.main:app --host $BindHost --port $Port $reload
