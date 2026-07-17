"""
API Router - Aggregates all v1 routers
"""
import logging

from fastapi import APIRouter, Depends
from app.api.v1 import icp, runs, analytics, jobs, pipelines, companies, matching, outreach, candidates, cost
from app.security.deps import require_auth

logger = logging.getLogger(__name__)

# Authentication is applied HERE, once, at the aggregate — not per-endpoint and
# not per-router. Two reasons:
#
#   1. Default-secure. Any router added below inherits it. Per-endpoint auth means
#      the protection is only as good as the next person's memory, and the failure
#      mode is a silently public endpoint serving candidate PII.
#   2. One place to read. "Is this API authenticated?" is answerable by looking at
#      this line, rather than auditing ~100 route decorators.
#
# /health is NOT under this router (it's mounted on the app in main.py), so Cloud
# Run's probe still works without a token.
#
api_router = APIRouter(dependencies=[Depends(require_auth)])

# Inbound provider callbacks (Apollo phone reveals, Smartlead, Cal.com). These
# CANNOT carry a bearer token — a third party has no way to get one — so they are
# mounted without the dependency and authenticate by signature instead.
#
# This router is a hole in the wall by construction. Keep it to callbacks that
# accept data; nothing here should ever read out candidate PII.
public_router = APIRouter()

# Include sub-routers
api_router.include_router(icp.router, prefix="/icp", tags=["ICP"])
api_router.include_router(runs.router, prefix="/runs", tags=["Runs"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["Jobs"])
api_router.include_router(pipelines.router, prefix="/pipelines", tags=["Pipelines"])
api_router.include_router(companies.router, prefix="/companies", tags=["Companies"])
api_router.include_router(matching.router, prefix="/matching", tags=["Matching"])
api_router.include_router(outreach.router, prefix="/outreach", tags=["Outreach"])
api_router.include_router(candidates.router, prefix="/candidates", tags=["Candidates"])
api_router.include_router(cost.router, prefix="/cost", tags=["Cost"])

# ── Unauthenticated provider callbacks ───────────────────────────────────────
# Same URL paths as before auth landed, so no provider dashboard needs updating:
#   POST /api/v1/jobs/prospects/mobile-webhook   (Apollo)
#   POST /api/v1/outreach/webhooks/smartlead
#   POST /api/v1/outreach/webhooks/calcom
public_router.include_router(jobs.webhook_router, prefix="/jobs", tags=["Webhooks"])
public_router.include_router(outreach.webhook_router, prefix="/outreach", tags=["Webhooks"])

# AI Engineer agent — OPTIONAL. Its third-party stack (pydantic-ai / MCP) can
# fail to import on a version mismatch; that must never take down the whole API.
# If it imports cleanly it's mounted at /agent; otherwise it's skipped (logged).
try:
    from app.api.v1 import agent
    api_router.include_router(agent.router, prefix="/agent", tags=["AI Engineer"])
except Exception as e:  # noqa: BLE001
    logger.warning("AI Engineer agent disabled — import failed: %s", e)
