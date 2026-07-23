"""GDPR API — data-subject rights over scraped candidate PII.

  POST /api/v1/gdpr/export   — DSAR: everything held about a person (Art. 15/20)
  POST /api/v1/gdpr/erase    — right to erasure across all collections (Art. 17)
  GET  /api/v1/gdpr/audit    — processing audit trail (admin-only, Art. 5(2))

A subject is identified by ANY of: candidateId, cvId, prospectId, email or
linkedinUrl. Export and erase are tenant-scoped — a client can only ever act on
data their own tenant holds; admins act across tenants. Every erase/export is
recorded in the append-only processing_audit log.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator

from app.database import get_database
from app.security.tenant import TenantContext, require_admin, tenant_scope
from app.services import gdpr_service

router = APIRouter()


async def get_db():
    return await get_database()


class SubjectRequest(BaseModel):
    """Identify a data subject by any one (or more) of these. At least one
    identifier is required — an empty body must never match 'everyone'."""
    candidateId: Optional[str] = None
    cvId: Optional[str] = None
    prospectId: Optional[str] = None
    email: Optional[str] = None
    linkedinUrl: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if not any([self.candidateId, self.cvId, self.prospectId, self.email, self.linkedinUrl]):
            raise ValueError("provide at least one identifier "
                             "(candidateId, cvId, prospectId, email or linkedinUrl)")
        return self

    def as_subject(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v}


@router.post("/export")
async def export_data(
    body: SubjectRequest,
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    """Return every PII record held about the subject (a DSAR response)."""
    try:
        return await gdpr_service.export_subject(db, ctx=ctx, subject=body.as_subject())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"export failed: {e}")


@router.post("/erase")
async def erase_data(
    body: SubjectRequest,
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    """Hard-delete the subject's PII across candidates, prospects, CVs, CV files
    and the enrichment cache. Irreversible; scoped to the caller's tenant."""
    try:
        result = await gdpr_service.erase_subject(db, ctx=ctx, subject=body.as_subject())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"erase failed: {e}")
    if result["totalDeleted"] == 0:
        # Nothing matched in the caller's scope — say so without revealing whether
        # the subject exists under some OTHER tenant.
        raise HTTPException(status_code=404, detail="no matching records in your workspace")
    return result


@router.get("/audit", dependencies=[Depends(require_admin)])
async def list_audit(limit: int = 100, db=Depends(get_db)):
    """Processing audit trail — who erased/exported what, when. Admin-only."""
    limit = max(1, min(int(limit or 100), 500))
    items = []
    async for d in db[gdpr_service.AUDIT].find({}).sort("occurredAt", -1).limit(limit):
        d["_id"] = str(d["_id"])
        if d.get("occurredAt"):
            d["occurredAt"] = d["occurredAt"].isoformat() + "Z"
        items.append(d)
    return {"items": items}
