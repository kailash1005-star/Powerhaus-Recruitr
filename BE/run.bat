@echo off
REM Start the backend using the VENV python (never the global one).
REM Usage:  run.bat   (serves http://127.0.0.1:8000 with --reload)
setlocal
set HERE=%~dp0
set VENVPY=%HERE%venv\Scripts\python.exe

if not exist "%VENVPY%" (
  echo [ERROR] venv python not found at %VENVPY%
  echo         Create it:  python -m venv venv ^&^& venv\Scripts\python.exe -m pip install -r requirements.txt
  exit /b 1
)

echo [run] Starting backend with venv python on 127.0.0.1:8000
"%VENVPY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
