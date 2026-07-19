# Start backend (:8000) + frontend (:3000) for local testing, each in its own
# window. Prereqs: BE\.env and UI\.env.local filled in (no <<FILL>> left).
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

foreach ($f in @("BE\.env", "UI\.env.local")) {
    $p = Join-Path $here $f
    if (-not (Test-Path $p)) { Write-Host "[ERROR] missing $f" -ForegroundColor Red; exit 1 }
    if (Select-String -Path $p -Pattern '<<FILL' -Quiet) {
        Write-Host "[ERROR] $f still has <<FILL>> placeholders — fill them first." -ForegroundColor Red; exit 1
    }
}

Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "Set-Location '$here\BE'; .\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "Set-Location '$here\UI'; npm run dev"

Write-Host ""
Write-Host "Backend  -> http://127.0.0.1:8000  (health: /health, API docs: /docs)" -ForegroundColor Cyan
Write-Host "Frontend -> http://localhost:3000" -ForegroundColor Cyan
