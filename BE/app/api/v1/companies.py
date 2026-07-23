"""
Companies API — minimal endpoints used by the candidate-pipeline UI.

GET /companies/{id}  — fetch a single company doc for modal prefill.
"""
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId

from app.database import get_database
from app.security.tenant import TenantContext, tenant_scope

router = APIRouter()


async def get_db():
    return await get_database()


@router.get("/{company_id}")
async def get_company(company_id: str, ctx: TenantContext = Depends(tenant_scope), db=Depends(get_db)):
    try:
        oid = ObjectId(company_id)
    except Exception:
        raise HTTPException(400, "Invalid company id")
    doc = await db["companies"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(404, "Company not found")
    # Companies are shared/deduped across runs (keyed by domain/slug), so they carry
    # no single tenantId. A caller may see a company only if their tenant has a job
    # referencing it — that's what prevents cross-tenant company enumeration. Admins
    # bypass. The pipeline UI also reads companies it created directly (tenantId set).
    if not ctx.is_admin and doc.get("tenantId") != ctx.tenant_id:
        owns = await db["jobs"].find_one(
            ctx.read_filter({"companyId": oid}), {"_id": 1}
        )
        if not owns:
            raise HTTPException(404, "Company not found")
    doc["_id"] = str(doc["_id"])
    return doc
