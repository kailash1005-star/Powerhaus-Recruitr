"""
FastAPI Application Entry Point
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.router import api_router, public_router
from app.database import connect_to_mongo, close_mongo_connection
from app.config import settings
from app.startup_checks import (
    auth_readiness,
    log_auth_readiness,
    log_matching_readiness,
    matching_readiness,
    outreach_readiness,
    verify_auth_configuration,
)

app = FastAPI(
    title="Recruitment API",
    description="Job hunting and recruitment automation API",
    version="1.0.0",
    redirect_slashes=False
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include v1 routers. Both are mounted under the same /api/v1 prefix so URLs are
# unchanged; they differ only in whether a bearer token is required.
app.include_router(api_router, prefix="/api/v1")       # authenticated
app.include_router(public_router, prefix="/api/v1")    # provider callbacks only


@app.get("/health")
async def health_check():
    """Health check endpoint — includes matching-engine readiness so a
    misconfigured environment (wrong Python / missing parser deps) is visible.

    Deliberately public: Cloud Run's health probe has no bearer token. It reports
    only configuration shape (is auth on, which issuer) — never secrets, and no
    data.
    """
    return {
        "status": "healthy",
        "matching": matching_readiness(),
        "outreach": outreach_readiness(),
        "auth": auth_readiness(),
    }

@app.on_event("startup")
async def startup_db_client():
    """Connect to MongoDB and verify the matching engine is runnable."""
    # First: refuse to boot on an unsafe auth configuration. Before Mongo, before
    # anything — a container that shouldn't be serving shouldn't get as far as
    # opening a database connection.
    verify_auth_configuration()
    log_auth_readiness()
    log_matching_readiness()
    await connect_to_mongo()
    # Seed + cache the cost price book for the Cost Analyser (never blocks startup).
    try:
        from app.services import cost_service
        await cost_service.init_price_book()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("[Cost] price book init skipped: %s", e)
    # Fail-forward any run orphaned by the previous process dying mid-flight —
    # otherwise the UI polls a "running" status no worker is executing, forever.
    from app.services.run_reaper import reap_stale_runs
    await reap_stale_runs()
    # GDPR: ensure the processing-audit indexes exist, then run the retention
    # sweep (no-op unless PII_RETENTION_DAYS > 0). Both are best-effort.
    try:
        from app.database import get_database
        from app.services import gdpr_service
        _db = await get_database()
        await gdpr_service.ensure_indexes(_db)
        await gdpr_service.apply_retention(_db, settings.PII_RETENTION_DAYS)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("[GDPR] startup step skipped: %s", e)
 
 
@app.on_event("shutdown")
async def shutdown_db_client():
    """Close MongoDB connection on shutdown"""
    await close_mongo_connection()
