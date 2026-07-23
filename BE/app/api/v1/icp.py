"""
ICP Configuration API Endpoints
GET  /api/v1/icp/config       - Fetch ICP configuration
POST /api/v1/icp/industries   - Add a new industry
POST /api/v1/icp/titles       - Add a new title
POST /api/v1/icp/locations    - Add a new location
"""
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from app.database import get_database
from app.security.tenant import TenantContext, tenant_scope
from app.schemas.icp_config import (
    ICPConfigResponseSchema,
    AddIndustryRequest,
    AddTitleRequest,
    AddLocationRequest,
)

router = APIRouter()


async def get_icp_collection():
    db = await get_database()
    return db["icpConfig"]


def _slugify(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


async def _active_or_latest(icp_collection, ctx: TenantContext):
    """Resolve the ICP config for this caller. A tenant sees its OWN config; if it
    has none yet it falls back to the shared default (legacy configs carry no
    tenantId) so the Campaigns UI is never empty. Admins see the global config."""
    if not ctx.is_admin:
        doc = (
            await icp_collection.find_one({"isActive": True, "tenantId": ctx.tenant_id})
            or await icp_collection.find_one({"tenantId": ctx.tenant_id}, sort=[("version", -1)])
        )
        if doc:
            return doc
        # No tenant-specific config — fall through to the shared default.
    doc = await icp_collection.find_one({"isActive": True})
    if not doc:
        doc = await icp_collection.find_one(sort=[("version", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="No ICP configuration found")
    return doc


async def _tenant_writable(icp_collection, ctx: TenantContext):
    """A config document this caller may safely mutate. Admins and a tenant that
    already owns its config edit it in place. A non-admin tenant that only has the
    SHARED default gets a private clone first — otherwise one client editing their
    target industries/titles would rewrite every other client's targeting."""
    doc = await _active_or_latest(icp_collection, ctx)
    if ctx.is_admin or doc.get("tenantId") == ctx.tenant_id:
        return doc
    from datetime import datetime as _dt
    clone = {k: v for k, v in doc.items() if k != "_id"}
    clone["tenantId"] = ctx.tenant_id
    clone["isActive"] = True
    clone["clonedFrom"] = doc["_id"]
    clone["createdAt"] = clone["updatedAt"] = _dt.utcnow()
    res = await icp_collection.insert_one(clone)
    clone["_id"] = res.inserted_id
    return clone


def _serialize(doc: dict) -> ICPConfigResponseSchema:
    doc["_id"] = str(doc["_id"])
    if "id" not in doc:
        doc["id"] = doc["_id"]
    return ICPConfigResponseSchema(**doc)


@router.get("/config", response_model=ICPConfigResponseSchema)
async def get_icp_config(ctx: TenantContext = Depends(tenant_scope), icp_collection=Depends(get_icp_collection)):
    try:
        return _serialize(await _active_or_latest(icp_collection, ctx))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching ICP config: {str(e)}")


@router.post("/industries", response_model=ICPConfigResponseSchema)
async def add_industry(body: AddIndustryRequest, ctx: TenantContext = Depends(tenant_scope), icp_collection=Depends(get_icp_collection)):
    if not body.displayName.strip():
        raise HTTPException(status_code=400, detail="displayName is required")
    doc = await _tenant_writable(icp_collection, ctx)
    slug = _slugify(body.displayName)
    existing_slugs = {i.get("slug") for i in (doc.get("industries") or [])}
    if slug in existing_slugs:
        # Already exists — idempotent
        return _serialize(doc)
    new_entry = {
        "slug": slug,
        "displayName": body.displayName.strip(),
        "isTarget": True,
        "linkedinNames": [],
        "description": (body.description or "").strip() or None,
    }
    await icp_collection.update_one(
        {"_id": doc["_id"]},
        {"$push": {"industries": new_entry}, "$set": {"updatedAt": datetime.utcnow()}},
    )
    return _serialize(await icp_collection.find_one({"_id": doc["_id"]}))


@router.post("/titles", response_model=ICPConfigResponseSchema)
async def add_title(body: AddTitleRequest, ctx: TenantContext = Depends(tenant_scope), icp_collection=Depends(get_icp_collection)):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    doc = await _tenant_writable(icp_collection, ctx)
    existing = {t.get("title", "").lower() for t in (doc.get("titles") or [])}
    if body.title.strip().lower() in existing:
        return _serialize(doc)
    new_entry = {"title": body.title.strip(), "isActive": True, "isDefault": True}
    await icp_collection.update_one(
        {"_id": doc["_id"]},
        {"$push": {"titles": new_entry}, "$set": {"updatedAt": datetime.utcnow()}},
    )
    return _serialize(await icp_collection.find_one({"_id": doc["_id"]}))


@router.post("/locations", response_model=ICPConfigResponseSchema)
async def add_location(body: AddLocationRequest, ctx: TenantContext = Depends(tenant_scope), icp_collection=Depends(get_icp_collection)):
    if not body.location.strip():
        raise HTTPException(status_code=400, detail="location is required")
    doc = await _tenant_writable(icp_collection, ctx)
    existing = {l.get("location", "").lower() for l in (doc.get("locations") or [])}
    if body.location.strip().lower() in existing:
        return _serialize(doc)
    new_entry = {
        "location": body.location.strip(),
        "country": (body.country or "").strip(),
        "isActive": True,
        "isDefault": True,
    }
    await icp_collection.update_one(
        {"_id": doc["_id"]},
        {"$push": {"locations": new_entry}, "$set": {"updatedAt": datetime.utcnow()}},
    )
    return _serialize(await icp_collection.find_one({"_id": doc["_id"]}))
