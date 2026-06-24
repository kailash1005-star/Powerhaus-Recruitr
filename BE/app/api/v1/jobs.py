"""
Jobs API Endpoints
GET  /api/v1/jobs                              - List all jobs (paginated, sortable)
GET  /api/v1/jobs/{job_id}/prospects           - Prospects for a job's company
POST /api/v1/jobs/prospects/{id}/enrich        - On-demand Apollo email enrichment
"""
import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import get_database
from app.services.apollo_service import ApolloService
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

        total = await jobs_col.count_documents({})
        skip = (page - 1) * limit

        cursor = (
            jobs_col.find()
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
