"""
Startup self-checks for the matching engine.

The #1 operational footgun is starting the backend with the WRONG Python (the
global interpreter instead of the venv), which lacks the parsing/numpy stack and
makes CV uploads silently fail. These helpers detect that early and loudly, and
let the API refuse uploads with a clear message instead of failing per-file in
the dark.
"""
from __future__ import annotations

import importlib.util
import sys

from app.config import settings

# Modules the matching engine needs. pinecone is only required when that backend
# is selected, so it is checked conditionally below. (pypdf + docx are the
# lightweight document parsers that replaced Docling.)
_CORE_MODULES = ["pypdf", "docx", "numpy", "multipart", "rapidfuzz", "openai"]


def matching_readiness() -> dict:
    """Return whether the current interpreter can run the matching engine."""
    missing = [m for m in _CORE_MODULES if importlib.util.find_spec(m) is None]
    if (settings.VECTOR_BACKEND or "mongo").lower() == "pinecone":
        if importlib.util.find_spec("pinecone") is None:
            missing.append("pinecone")

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    return {
        "ready": len(missing) == 0,
        "missing": missing,
        "pythonExe": sys.executable,
        "inVenv": in_venv,
        "vectorBackend": settings.VECTOR_BACKEND,
    }


def outreach_readiness() -> dict:
    """Report what the Outreach CRM has wired. Nothing here blocks the UI — the
    CRM renders empty when unconfigured; webhooks work without an API key, and
    *_WEBHOOK_SECRET only turns on signature verification."""
    return {
        "provider": settings.OUTREACH_PROVIDER,
        "sendEnabled": bool(settings.SMARTLEAD_API_KEY),
        "smartleadWebhookVerified": bool(settings.SMARTLEAD_WEBHOOK_SECRET),
        "calcomWebhookVerified": bool(settings.CALCOM_WEBHOOK_SECRET),
    }


def log_matching_readiness() -> dict:
    """Print a prominent banner at startup if the engine isn't ready."""
    status = matching_readiness()
    if status["ready"]:
        print(f"[OK] Matching engine ready (python: {status['pythonExe']})")
        return status

    bar = "=" * 72
    print(bar)
    print("[FATAL-ish] MATCHING ENGINE NOT READY — CV uploads will FAIL.")
    print(f"            Missing packages: {', '.join(status['missing'])}")
    print(f"            Running Python  : {status['pythonExe']}")
    print(f"            In virtualenv?  : {status['inVenv']}")
    print("            You are likely running the GLOBAL python. Start with the venv:")
    print("              .\\venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000")
    print("            or just run the launcher:  .\\run.ps1   (Windows)  /  ./run.sh")
    print(bar)
    return status
