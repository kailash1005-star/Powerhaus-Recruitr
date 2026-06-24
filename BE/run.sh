#!/usr/bin/env bash
# Start the backend using the VENV python (never the global one).
# Usage:  ./run.sh           -> http://0.0.0.0:8000
#         PORT=8080 ./run.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer venv; fall back to the active interpreter (e.g. inside a Cloud Run image
# where deps are installed system-wide).
if [ -x "$HERE/venv/Scripts/python.exe" ]; then
  PY="$HERE/venv/Scripts/python.exe"          # Windows venv
elif [ -x "$HERE/venv/bin/python" ]; then
  PY="$HERE/venv/bin/python"                   # Linux/macOS venv
else
  PY="python"
fi

PORT="${PORT:-8000}"
echo "[run] Starting backend with: $PY  on 0.0.0.0:${PORT}"
exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
