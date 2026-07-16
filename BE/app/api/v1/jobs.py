"""
Jobs API Endpoints
GET  /api/v1/jobs                              - List all jobs (paginated, sortable, ?q search)
POST /api/v1/jobs                              - Create a manual job entry
GET  /api/v1/jobs/{job_id}/prospects           - Prospects for a job's company
POST /api/v1/jobs/prospects/{id}/enrich        - On-demand Apollo email enrichment
"""
import asyncio
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.database import get_database
from app.services.apollo_service import ApolloService
from app.config import settings
from bson import ObjectId

router = APIRouter()


async def get_db():
    return await get_database()


# ── GET / (list all jobs, paginated + sortable) ────────────────────────
@router.get("")
async def list_jobs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    sort_by: str = Query(
        "createdAt",
        description="Sort field: title|company|location|boardName|qualityStatus|createdAt",
    ),
    sort_order: str = Query("desc", description="Sort order: asc|desc"),
    q: str = Query(None, description="Typeahead: matches title or location"),
    db=Depends(get_db),
):
    """
    Return paginated jobs from the entire jobs collection.
    Supports sorting by any column the UI needs.
    """
    try:
        jobs_col = db["jobs"]

        # Map UI field names to MongoDB field names
        field_map = {
            "title": "title",
            "company": "company",
            "location": "location",
            "boardName": "boardName",
            "jobBoard": "boardName",
            "qualityStatus": "qualityStatus",
            "quality": "qualityStatus",
            "createdAt": "createdAt",
            "postedDate": "createdAt",
        }
        mongo_field = field_map.get(sort_by, "createdAt")
        sort_direction = 1 if sort_order == "asc" else -1

        query = {}
        if q:
            regex = {"$regex": q.strip(), "$options": "i"}
            query["$or"] = [{"title": regex}, {"location": regex}]

        total = await jobs_col.count_documents(query)
        skip = (page - 1) * limit

        cursor = (
            jobs_col.find(query)
            .sort(mongo_field, sort_direction)
            .skip(skip)
            .limit(limit)
        )

        jobs = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            if doc.get("runId"):
                doc["runId"] = str(doc["runId"])
            if doc.get("companyId"):
                doc["companyId"] = str(doc["companyId"])
            if doc.get("canonicalId"):
                doc["canonicalId"] = str(doc["canonicalId"])
            jobs.append(doc)

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "pages": max(1, (total + limit - 1) // limit),
            "jobs": jobs,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching jobs: {str(e)}")


# ── POST / (create manual job) ────────────────────────────────────────


class ManualJobCreateSchema(BaseModel):
    title: str
    location: str = ""
    companyId: Optional[str] = None
    description: Optional[str] = None


@router.post("")
async def create_manual_job(body: ManualJobCreateSchema, db=Depends(get_db)):
    """Create a manual job entry (not from a scraping run)."""
    try:
        jobs_col = db["jobs"]
        now = datetime.utcnow()
        doc = {
            "title": body.title,
            "location": body.location or "",
            "boardName": "manual",
            "qualityStatus": "good",
            "source": "manual",
            "createdAt": now,
            "updatedAt": now,
        }
        # The `jobs` schema validates jobDetails as bsonType "object"; a null
        # value fails validation, so only include it when there's a description.
        if body.description:
            doc["jobDetails"] = {"description": body.description}
        # The `jobs` collection schema requires companyId to be a real ObjectId
        # (not a string), so cast it — and only include it when valid.
        if body.companyId:
            try:
                doc["companyId"] = ObjectId(body.companyId)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid companyId")
        res = await jobs_col.insert_one(doc)
        created = await jobs_col.find_one({"_id": res.inserted_id})
        created["_id"] = str(created["_id"])
        if created.get("companyId"):
            created["companyId"] = str(created["companyId"])
        return created
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating job: {str(e)}")


# ── GET /{job_id}/prospects ────────────────────────────────────────────
@router.get("/{job_id}/prospects")
async def get_job_prospects(job_id: str, db=Depends(get_db)):
    """Return prospects linked to the job's company. emailTemplate is stubbed."""
    try:
        job_oid = ObjectId(job_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid job id")

    job = await db["jobs"].find_one({"_id": job_oid}, {"companyId": 1})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    company_id = job.get("companyId")
    prospects: list = []
    if company_id:
        cursor = db["prospects"].find({"companyId": company_id})
        async for p in cursor:
            p["_id"] = str(p["_id"])
            if p.get("runId"):
                p["runId"] = str(p["runId"])
            if p.get("companyId"):
                p["companyId"] = str(p["companyId"])
            prospects.append(p)

    return {"prospects": prospects, "emailTemplate": None}


# ── POST /prospects/{prospect_id}/enrich ───────────────────────────────
# Apollo's placeholder when contact info isn't unlocked.
_LOCKED_EMAIL_MARKERS = ("email_not_unlocked", "@domain.com")


def _best_prospect_email(person: dict) -> str | None:
    """Pick a usable email from an Apollo /people/match person payload.

    Apollo returns ``email`` (work) and ``personal_emails``; when contact info
    is still locked it returns a placeholder like
    ``email_not_unlocked@domain.com`` which we must not surface as real.
    """
    email = (person.get("email") or "").strip()
    if email and not any(m in email.lower() for m in _LOCKED_EMAIL_MARKERS):
        return email
    for pe in person.get("personal_emails") or []:
        if pe and pe.strip():
            return pe.strip()
    return None


@router.post("/prospects/{prospect_id}/enrich")
async def enrich_prospect(prospect_id: str, db=Depends(get_db)):
    """On-demand Apollo enrichment for a single prospect — reveals their email.

    The free Apollo people-search that originally found this prospect masks all
    contact info. This calls Apollo ``/people/match`` (credit-consuming, with
    ``reveal_personal_emails``) to unlock the email + LinkedIn + phone, then
    persists them on the prospect document so the UI can use the email to reach
    out. Mirrors the candidate enrichment flow but for the ``prospects``
    collection.
    """
    try:
        oid = ObjectId(prospect_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid prospect id")

    col = db["prospects"]
    prospect = await col.find_one({"_id": oid})
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    apollo_id = prospect.get("apolloId")
    if not apollo_id:
        raise HTTPException(status_code=400, detail="Prospect has no apolloId to enrich")

    svc = ApolloService()
    # _enrich_single → Apollo /people/match with reveal_personal_emails=True.
    person = await asyncio.to_thread(svc._enrich_single, {"id": apollo_id})
    if not person:
        raise HTTPException(status_code=502, detail="Apollo enrichment returned no data")

    email = _best_prospect_email(person)

    details = dict(prospect.get("prospectDetails") or {})
    if person.get("linkedin_url"):
        details["linkedinUrl"] = person["linkedin_url"]
    if person.get("formatted_address"):
        details["location"] = person["formatted_address"]
    phones = person.get("phone_numbers") or []
    if phones:
        raw = phones[0]
        if isinstance(raw, dict):
            details["phone"] = raw.get("sanitized_number") or raw.get("raw_number")
        elif isinstance(raw, str):
            details["phone"] = raw

    update: dict = {
        "isEnriched": True,
        "prospectDetails": details,
        "updatedAt": datetime.utcnow(),
    }
    if email:
        update["email"] = email
    # Backfill identity fields the masked search may have left blank.
    if person.get("first_name") and not prospect.get("firstName"):
        update["firstName"] = person["first_name"]
    if person.get("last_name") and not (prospect.get("lastName") or "").strip():
        update["lastName"] = person["last_name"]
    if person.get("title") and not prospect.get("title"):
        update["title"] = person["title"]
    if person.get("seniority") and not prospect.get("seniority"):
        update["seniority"] = person["seniority"]

    await col.update_one({"_id": oid}, {"$set": update})

    out = await col.find_one({"_id": oid})
    out["_id"] = str(out["_id"])
    if out.get("runId"):
        out["runId"] = str(out["runId"])
    if out.get("companyId"):
        out["companyId"] = str(out["companyId"])
    return {"prospect": out, "emailRevealed": bool(email)}


# ── POST /prospects/{prospect_id}/enrich-mobile ─────────────────────────
@router.post("/prospects/{prospect_id}/enrich-mobile")
async def enrich_prospect_mobile(prospect_id: str, db=Depends(get_db)):
    """Reveal a prospect's mobile number via Apollo (reveal_phone_number=True).

    Apollo may return the number immediately (cached) — saved at once — or deliver
    it asynchronously to APOLLO_WEBHOOK_URL, in which case the prospect is marked
    ``mobileEnrichmentStatus: "pending"`` and filled in by /prospects/mobile-webhook.
    """
    try:
        oid = ObjectId(prospect_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid prospect id")

    webhook_url = settings.APOLLO_WEBHOOK_URL or ""
    if not webhook_url:
        raise HTTPException(
            status_code=503,
            detail=("APOLLO_WEBHOOK_URL is not configured. Set it in .env to a publicly "
                    "reachable URL (e.g. an ngrok tunnel for local dev) ending in "
                    "/api/v1/jobs/prospects/mobile-webhook so Apollo can deliver phone numbers."),
        )

    col = db["prospects"]
    companies_col = db["companies"]
    prospect = await col.find_one({"_id": oid})
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    details = dict(prospect.get("prospectDetails") or {})
    if details.get("phone"):
        return {"prospect_id": prospect_id, "status": "enriched", "phone": details["phone"]}

    # Best-effort company name/domain to disambiguate the Apollo match.
    org = None
    if prospect.get("companyId"):
        company = await companies_col.find_one({"_id": prospect["companyId"]})
        if company:
            dom = company.get("companyDomain") or ""
            org = dom if dom and not dom.endswith(".linkedin.local") else company.get("companyName")

    svc = ApolloService()
    person = await asyncio.to_thread(
        lambda: svc.match_phone(
            apollo_id=prospect.get("apolloId"),
            email=prospect.get("email") or None,
            first_name=prospect.get("firstName") or None,
            last_name=prospect.get("lastName") or None,
            organization_name=org,
            webhook_url=webhook_url,
        )
    )
    if person is None:
        raise HTTPException(status_code=502, detail="Apollo phone match request failed")

    now = datetime.utcnow()
    found_phone = ApolloService.extract_mobile(person)
    if found_phone:
        details["phone"] = found_phone
        await col.update_one(
            {"_id": oid},
            {"$set": {"prospectDetails": details, "mobileEnrichmentStatus": "enriched", "updatedAt": now}},
        )
        return {"prospect_id": prospect_id, "status": "enriched", "phone": found_phone}

    # No number yet → Apollo will POST it to the webhook.
    await col.update_one(
        {"_id": oid},
        {"$set": {"mobileEnrichmentStatus": "pending", "updatedAt": now}},
    )
    return {"prospect_id": prospect_id, "status": "pending", "phone": None}


# ── POST /prospects/mobile-webhook ──────────────────────────────────────
@router.post("/prospects/mobile-webhook")
async def apollo_mobile_webhook(payload: dict, db=Depends(get_db)):
    """Public receiver for Apollo's asynchronous phone-number delivery."""
    if payload.get("status") and payload.get("status") != "success":
        return {"status": "ignored", "reason": f"status is {payload.get('status')}"}

    col = db["prospects"]
    now = datetime.utcnow()
    updated = 0
    for person in payload.get("people", []) or []:
        apollo_id = person.get("id")
        phone = ApolloService.extract_mobile(person)
        if not apollo_id or not phone:
            continue
        # Update the copies that triggered this reveal (pending), across runs.
        cursor = col.find({"apolloId": apollo_id, "mobileEnrichmentStatus": "pending"})
        async for p in cursor:
            details = dict(p.get("prospectDetails") or {})
            details["phone"] = phone
            await col.update_one(
                {"_id": p["_id"]},
                {"$set": {"prospectDetails": details, "mobileEnrichmentStatus": "enriched", "updatedAt": now}},
            )
            updated += 1
    return {"status": "ok", "updated": updated}
