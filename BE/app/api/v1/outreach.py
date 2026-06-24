"""
Outreach CRM API — powers Outreach → Leads / Candidates.

  GET  /api/v1/outreach            — read model (audience=leads|candidates)
  GET  /api/v1/outreach/metrics    — funnel counts for the KPI strip
  GET  /api/v1/outreach/config     — what's configured (sending / verification)
  POST /api/v1/outreach/enroll     — push a contact into the sending campaign
  POST /api/v1/outreach/webhooks/smartlead — Smartlead event ingestion
  POST /api/v1/outreach/webhooks/calcom    — Cal.com meeting ingestion
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.config import settings
from app.database import get_database
from app.services import outreach_service
from app.services.outreach_provider import get_source

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_db():
    return await get_database()


def _audience(value: str) -> str:
    v = (value or "").lower()
    return "candidate" if v in ("candidates", "candidate") else "lead"


@router.get("")
async def list_outreach(
    audience: str = Query("leads"),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
):
    return await outreach_service.list_messages(db, _audience(audience), status, page, limit)


@router.get("/metrics")
async def outreach_metrics(audience: str = Query("leads"), db=Depends(get_db)):
    return await outreach_service.metrics(db, _audience(audience))


@router.get("/config")
async def outreach_config():
    """Surface what's wired so the UI can render an honest state."""
    return {
        "provider": settings.OUTREACH_PROVIDER,
        "sendEnabled": outreach_service.outreach_configured(),
        "smartleadWebhookVerified": bool(settings.SMARTLEAD_WEBHOOK_SECRET),
        "calcomWebhookVerified": bool(settings.CALCOM_WEBHOOK_SECRET),
    }


class EnrollRequest(BaseModel):
    email: str
    name: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    roleTitle: Optional[str] = None
    audience: str = "lead"
    campaignId: Optional[str] = None
    campaignName: Optional[str] = None
    leadId: Optional[str] = None
    candidateId: Optional[str] = None


@router.post("/enroll")
async def enroll(req: EnrollRequest, db=Depends(get_db)):
    try:
        return await outreach_service.enroll(db, {**req.model_dump(), "audience": _audience(req.audience)})
    except RuntimeError as e:
        # Not configured — return a clear, non-fatal signal.
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.exception("[Outreach] enroll failed")
        return {"ok": False, "error": str(e)}


# ── Webhooks ─────────────────────────────────────────────────────────────────
async def _ingest_webhook(request: Request, provider_name: str, db) -> dict:
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    provider = get_source(provider_name)

    if not provider.verify_signature(headers, raw):
        logger.warning("[Outreach] %s webhook signature rejected", provider_name)
        return {"ok": False, "error": "signature verification failed"}

    try:
        body = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid JSON"}

    events = provider.normalize(body)
    results = []
    for ev in events:
        results.append(await outreach_service.ingest_event(db, ev))
    return {"ok": True, "ingested": len(results), "results": results}


@router.post("/webhooks/smartlead")
async def smartlead_webhook(request: Request, db=Depends(get_db)):
    return await _ingest_webhook(request, "smartlead", db)


@router.post("/webhooks/calcom")
async def calcom_webhook(request: Request, db=Depends(get_db)):
    return await _ingest_webhook(request, "calcom", db)
