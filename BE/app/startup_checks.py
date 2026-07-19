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


def auth_readiness() -> dict:
    """Report the auth configuration. Surfaced on /health."""
    return {
        "enabled": settings.AUTH_ENABLED,
        "configured": settings.auth0_configured,
        "issuer": settings.auth0_issuer or None,
        "audience": settings.AUTH0_AUDIENCE or None,
    }


class AuthMisconfigured(RuntimeError):
    """Auth configuration is unsafe or unusable. Raised at startup, never at
    request time — a misconfigured deployment must not boot and then quietly
    serve candidate data to anyone who asks."""


def verify_auth_configuration() -> None:
    """Refuse to start on an unsafe auth configuration.

    This exists because AUTH_ENABLED is a foot-gun. A flag that can silently
    switch authentication off is fine in a test suite and catastrophic in
    production, and the failure is invisible — the app works perfectly, it just
    serves everyone. So the flag is only honoured when nothing about the
    environment looks like production.

    Two ways to be wrong, both fatal:
      1. Auth ON but not configured  → every request 401s. Fail now, loudly,
         rather than after deploy with a mystery.
      2. Auth OFF but an Auth0 tenant IS configured → almost certainly a
         production env someone disabled auth in. Never boot.
    """
    if settings.AUTH_ENABLED and not settings.auth0_configured:
        raise AuthMisconfigured(
            "AUTH_ENABLED=true but Auth0 is not configured. "
            "Set AUTH0_DOMAIN and AUTH0_AUDIENCE (see docs/engineering/AUTH0_SETUP.md), "
            "or set AUTH_ENABLED=false for local development."
        )

    if not settings.AUTH_ENABLED and settings.auth0_configured:
        raise AuthMisconfigured(
            "AUTH_ENABLED=false while AUTH0_DOMAIN is set. Refusing to start: "
            "this would serve every endpoint unauthenticated against what looks "
            "like a real deployment. Unset AUTH0_DOMAIN for local dev, or set "
            "AUTH_ENABLED=true."
        )


def log_auth_readiness() -> dict:
    """Banner the auth state at startup. Unauthenticated mode must be impossible
    to run for weeks without noticing."""
    status = auth_readiness()
    if status["enabled"]:
        print(f"[OK] Auth enabled — issuer {status['issuer']}, audience {status['audience']}")
        return status

    bar = "!" * 72
    print(bar)
    print("[WARNING] AUTH IS DISABLED — every API endpoint is OPEN.")
    print("          Every request runs as the dev principal (dev|local).")
    print("          This is for LOCAL DEVELOPMENT ONLY. If you are seeing this")
    print("          anywhere else, stop the service now.")
    print(bar)
    return status


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
