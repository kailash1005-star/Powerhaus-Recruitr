"""
Candidate Pipelines API

Endpoints for the recruitment candidate-sourcing product:

  GET    /pipelines                                — list (paginated, ?q for typeahead)
  POST   /pipelines                                — create (from companyId OR manual)
  GET    /pipelines/{id}                           — detail (with embedded jobs[])
  DELETE /pipelines/{id}                           — cascade-delete pipeline + candidates
  POST   /pipelines/{id}/jobs                      — add job, spawn background search
  DELETE /pipelines/{id}/jobs/{jobId}              — remove job + its sole candidates
  POST   /pipelines/{id}/jobs/{jobId}/rerun        — re-run candidate search for a job
  GET    /pipelines/{id}/jobs/{jobId}/candidates   — paginated candidates for a job

  PATCH  /candidates/{id}                          — accept/reject (+ reason)
  POST   /candidates/{id}/enrich                   — Apollo /people/match enrichment
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.database import get_database
from app.services.candidate_pipeline import (
    add_job_to_pipeline,
    rerun_job_search,
)

router = APIRouter()


async def get_db():
    return await get_database()


# ── Schemas ──────────────────────────────────────────────────────────────


class PipelineCreateSchema(BaseModel):
    """Create a pipeline either by linking an existing company (companyId) or
    by supplying the company details manually (companyName + companyDomain
    are required in that case)."""
    companyId: Optional[str] = None
    companyName: Optional[str] = None
    companyDomain: Optional[str] = None
    companyIndustry: Optional[str] = ""
    matchedIndustry: Optional[str] = None
    companyLocation: Optional[str] = ""
    linkedinSlug: Optional[str] = None
    website: Optional[str] = ""


class PipelineAddJobSchema(BaseModel):
    jobId: str


class CandidatePatchSchema(BaseModel):
    isAccepted: Optional[bool] = None
    rejectionReason: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────


def _serialize_pipeline(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    if doc.get("companyId") and not isinstance(doc["companyId"], str):
        doc["companyId"] = str(doc["companyId"])
    return doc


def _serialize_candidate(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    return doc


# ── GET / (list, paginated, ?q typeahead) ────────────────────────────────


@router.get("")
async def list_pipelines(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    q: Optional[str] = Query(None, description="Typeahead: matches companyName/companyDomain"),
    db=Depends(get_db),
):
    try:
        col = db["candidatePipelines"]
        query: Dict[str, Any] = {}
        if q:
            # Case-insensitive prefix match on companyName / companyDomain
            regex = {"$regex": q.strip(), "$options": "i"}
            query["$or"] = [{"companyName": regex}, {"companyDomain": regex}]
        total = await col.count_documents(query)
        skip = (page - 1) * limit
        cursor = col.find(query).sort("updatedAt", -1).skip(skip).limit(limit)
        items: List[dict] = []
        async for doc in cursor:
            items.append(_serialize_pipeline(doc))
        return {
            "total": total, "page": page, "limit": limit,
            "pages": max(1, (total + limit - 1) // limit),
            "pipelines": items,
        }
    except Exception as e:
        raise HTTPException(500, f"Error listing pipelines: {e}")


# ── POST / (create) ──────────────────────────────────────────────────────


@router.post("")
async def create_pipeline(body: PipelineCreateSchema, db=Depends(get_db)):
    try:
        pipelines = db["candidatePipelines"]
        companies = db["companies"]
        now = datetime.utcnow()

        company: Optional[dict] = None
        source = "manual"

        # Mode 1: link an existing company
        if body.companyId:
            try:
                company = await companies.find_one({"_id": ObjectId(body.companyId)})
            except Exception:
                raise HTTPException(400, "Invalid companyId")
            if not company:
                raise HTTPException(404, "Company not found")
            source = "run"

        # Mode 2: manual — need name + domain
        if company is None:
            if not body.companyName or not body.companyDomain:
                raise HTTPException(
                    400,
                    "Either companyId, or companyName + companyDomain are required",
                )
            # Reuse a company by domain if it happens to exist already
            company = await companies.find_one({"companyDomain": body.companyDomain})
            if company is None:
                # Synthetic company — created so the pipeline has a stable companyId.
                # Marked source: "manual" so it's distinguishable from Phase-2-resolved ones.
                synth = {
                    "companyName": body.companyName,
                    "companyDomain": body.companyDomain,
                    "companyIndustry": body.companyIndustry or "",
                    "industry": body.companyIndustry or "",
                    "matchedIndustry": body.matchedIndustry,
                    "website": body.website or "",
                    "linkedinSlug": body.linkedinSlug,
                    "location": body.companyLocation or "",
                    "isEligible": None,
                    "targeted": False,
                    "source": "manual",
                    "createdAt": now, "updatedAt": now,
                }
                res = await companies.insert_one({k: v for k, v in synth.items() if v is not None})
                company = await companies.find_one({"_id": res.inserted_id})

        # Reject if a pipeline already exists for this company
        existing = await pipelines.find_one({
            "$or": [
                {"companyId": str(company["_id"])},
                {"companyDomain": company.get("companyDomain")},
            ]
        })
        if existing:
            raise HTTPException(
                409,
                {"error": "pipeline_already_exists", "pipelineId": str(existing["_id"])},
            )

        # Resolve matchedIndustry: prefer the one passed in, else from the company
        matched_industry = body.matchedIndustry or company.get("matchedIndustry")

        # Use the company HQ location we extract; allow body override
        company_location = body.companyLocation or company.get("location") or ""

        doc = {
            "companyId": str(company["_id"]),
            "companyName": company.get("companyName") or body.companyName or "",
            "companyDomain": company.get("companyDomain") or body.companyDomain or "",
            "companyIndustry": company.get("industry") or body.companyIndustry or "",
            "matchedIndustry": matched_industry,
            "companyLocation": company_location,
            "linkedinSlug": company.get("linkedinSlug") or body.linkedinSlug,
            "website": company.get("website") or body.website or "",
            "source": source,
            "jobs": [],
            "totalCandidates": 0,
            "acceptedCount": 0,
            "rejectedCount": 0,
            "createdAt": now, "updatedAt": now,
        }
        try:
            res = await pipelines.insert_one(doc)
        except DuplicateKeyError:
            # Another caller raced us
            again = await pipelines.find_one({"companyDomain": doc["companyDomain"]})
            raise HTTPException(
                409, {"error": "pipeline_already_exists", "pipelineId": str(again["_id"])},
            )
        out = await pipelines.find_one({"_id": res.inserted_id})
        return _serialize_pipeline(out)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error creating pipeline: {e}")


# ── GET /{id} ────────────────────────────────────────────────────────────


@router.get("/{pipeline_id}")
async def get_pipeline(pipeline_id: str, db=Depends(get_db)):
    try:
        col = db["candidatePipelines"]
        doc = await col.find_one({"_id": ObjectId(pipeline_id)})
        if not doc:
            raise HTTPException(404, "Pipeline not found")
        return _serialize_pipeline(doc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error fetching pipeline: {e}")


# ── DELETE /{id} (cascade) ───────────────────────────────────────────────


@router.delete("/{pipeline_id}")
async def delete_pipeline(pipeline_id: str, db=Depends(get_db)):
    try:
        pipelines = db["candidatePipelines"]
        candidates = db["candidates"]
        oid = ObjectId(pipeline_id)
        cand_res = await candidates.delete_many({"pipelineId": pipeline_id})
        pipe_res = await pipelines.delete_one({"_id": oid})
        if pipe_res.deleted_count == 0:
            raise HTTPException(404, "Pipeline not found")
        return {
            "success": True,
            "deleted": {"candidates": cand_res.deleted_count},
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error deleting pipeline: {e}")


# ── POST /{id}/jobs (add job + spawn search) ─────────────────────────────


@router.post("/{pipeline_id}/jobs")
async def add_job(pipeline_id: str, body: PipelineAddJobSchema):
    try:
        result = await add_job_to_pipeline(pipeline_id, body.jobId)
        return {"success": True, **result}
    except ValueError as ve:
        code = str(ve)
        status = {
            "pipeline_not_found": 404,
            "job_not_found": 404,
            "job_already_in_pipeline": 409,
        }.get(code, 400)
        raise HTTPException(status, code)
    except Exception as e:
        raise HTTPException(500, f"Error adding job: {e}")


# ── DELETE /{id}/jobs/{jobId} ────────────────────────────────────────────


@router.delete("/{pipeline_id}/jobs/{job_id}")
async def remove_job(pipeline_id: str, job_id: str, db=Depends(get_db)):
    """Remove a job from a pipeline.

    Candidates whose ``sourceJobIds`` is just this one job → deleted.
    Candidates surfaced by other jobs too → keep, just trim sourceJobIds.
    """
    try:
        pipelines = db["candidatePipelines"]
        candidates = db["candidates"]
        oid = ObjectId(pipeline_id)
        now = datetime.utcnow()

        # 1. Pull the job out of the embedded array
        res = await pipelines.update_one(
            {"_id": oid},
            {"$pull": {"jobs": {"jobId": job_id}}, "$set": {"updatedAt": now}},
        )
        if res.modified_count == 0:
            raise HTTPException(404, "Job not in this pipeline")

        # 2. Delete candidates uniquely tied to this job
        deleted = await candidates.delete_many({
            "pipelineId": pipeline_id,
            "sourceJobIds": [job_id],  # exact match: list of exactly one item
        })
        # 3. Detach this jobId from candidates surfaced by multiple jobs
        await candidates.update_many(
            {"pipelineId": pipeline_id, "sourceJobIds": job_id},
            {"$pull": {"sourceJobIds": job_id}, "$set": {"updatedAt": now}},
        )

        # 4. Roll up pipeline totals
        total = await candidates.count_documents({"pipelineId": pipeline_id})
        accepted = await candidates.count_documents({"pipelineId": pipeline_id, "isAccepted": True})
        rejected = await candidates.count_documents({"pipelineId": pipeline_id, "isAccepted": False})
        await pipelines.update_one(
            {"_id": oid},
            {"$set": {
                "totalCandidates": total,
                "acceptedCount": accepted,
                "rejectedCount": rejected,
                "updatedAt": now,
            }},
        )
        return {"success": True, "deleted_candidates": deleted.deleted_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error removing job: {e}")


# ── POST /{id}/jobs/{jobId}/rerun ────────────────────────────────────────


@router.post("/{pipeline_id}/jobs/{job_id}/rerun")
async def rerun_job(pipeline_id: str, job_id: str):
    try:
        result = await rerun_job_search(pipeline_id, job_id)
        return {"success": True, **result}
    except ValueError as ve:
        code = str(ve)
        # "busy" → already running (or job not found in pipeline)
        raise HTTPException(409 if code == "busy" else 400, code)
    except Exception as e:
        raise HTTPException(500, f"Error rerunning search: {e}")


# ── GET /{id}/jobs/{jobId}/candidates ────────────────────────────────────


@router.get("/{pipeline_id}/jobs/{job_id}/candidates")
async def list_job_candidates(
    pipeline_id: str,
    job_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    quality: Optional[str] = Query(None, description="all|accepted|rejected"),
    sort_by: str = Query("matchScore", description="matchScore|createdAt"),
    sort_order: str = Query("desc"),
    db=Depends(get_db),
):
    try:
        col = db["candidates"]
        query: Dict[str, Any] = {"pipelineId": pipeline_id, "sourceJobIds": job_id}
        if quality == "accepted":
            query["isAccepted"] = True
        elif quality == "rejected":
            query["isAccepted"] = False
        total = await col.count_documents(query)
        skip = (page - 1) * limit
        direction = 1 if sort_order == "asc" else -1
        cursor = col.find(query).sort(sort_by, direction).skip(skip).limit(limit)
        items: List[dict] = []
        async for doc in cursor:
            items.append(_serialize_candidate(doc))
        return {
            "total": total, "page": page, "limit": limit,
            "pages": max(1, (total + limit - 1) // limit),
            "candidates": items,
        }
    except Exception as e:
        raise HTTPException(500, f"Error listing candidates: {e}")


# ── PATCH /candidates/{id} (accept/reject) ───────────────────────────────


@router.patch("/candidates/{candidate_id}")
async def patch_candidate(candidate_id: str, body: CandidatePatchSchema, db=Depends(get_db)):
    try:
        col = db["candidates"]
        pipelines = db["candidatePipelines"]
        oid = ObjectId(candidate_id)
        cand = await col.find_one({"_id": oid})
        if not cand:
            raise HTTPException(404, "Candidate not found")

        update: Dict[str, Any] = {"updatedAt": datetime.utcnow()}
        if body.isAccepted is not None:
            update["isAccepted"] = body.isAccepted
            update["decidedAt"] = datetime.utcnow()
            update["rejectionReason"] = body.rejectionReason if not body.isAccepted else None
        elif body.rejectionReason is not None:
            update["rejectionReason"] = body.rejectionReason

        await col.update_one({"_id": oid}, {"$set": update})

        # Roll up pipeline totals + per-job counts
        pipeline_id = cand.get("pipelineId")
        if pipeline_id:
            total = await col.count_documents({"pipelineId": pipeline_id})
            accepted = await col.count_documents({"pipelineId": pipeline_id, "isAccepted": True})
            rejected = await col.count_documents({"pipelineId": pipeline_id, "isAccepted": False})
            await pipelines.update_one(
                {"_id": ObjectId(pipeline_id)},
                {"$set": {
                    "totalCandidates": total,
                    "acceptedCount": accepted,
                    "rejectedCount": rejected,
                    "updatedAt": datetime.utcnow(),
                }},
            )

        out = await col.find_one({"_id": oid})
        return _serialize_candidate(out)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error updating candidate: {e}")


# ── POST /candidates/{id}/enrich ─────────────────────────────────────────


@router.post("/candidates/{candidate_id}/enrich")
async def enrich_candidate(candidate_id: str, db=Depends(get_db)):
    """Manual enrichment — calls Apollo /people/match for one person.

    Storage policy:
      1. ``enrichedRaw`` — the FULL untouched Apollo response (top-level wrapper
         + every key of the ``person`` object). Audit-grade; never trimmed.
      2. ``enrichedData`` — a small UI-friendly subset that mirrors what
         /people/match actually returns. Apollo does NOT return education,
         skills, summary, openToWork, or a candidate-level phone — those keys
         live on the org or simply don't exist. We only project fields that
         really appear in the payload.
    """
    import asyncio
    import requests as _req
    from app.config import APOLLO_BASE_URL, settings as _settings
    try:
        col = db["candidates"]
        oid = ObjectId(candidate_id)
        cand = await col.find_one({"_id": oid})
        if not cand:
            raise HTTPException(404, "Candidate not found")
        if not cand.get("apolloId"):
            raise HTTPException(400, "Candidate has no apolloId")

        # Direct Apollo call so we can capture the FULL response envelope, not
        # just the ``person`` slice that ApolloService._enrich_single returns.
        def _call() -> dict | None:
            resp = _req.post(
                f"{APOLLO_BASE_URL}/people/match",
                headers={
                    "x-api-key": _settings.APOLLO_API_KEY,
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache",
                },
                json={"id": cand["apolloId"], "reveal_personal_emails": True},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

        raw_envelope = await asyncio.to_thread(_call)
        if not raw_envelope or not raw_envelope.get("person"):
            raise HTTPException(502, "Apollo enrichment returned no data")

        person = raw_envelope["person"]
        org = person.get("organization") or {}
        now = datetime.utcnow()

        # ── Structured projection (fields Apollo actually returns) ──────────
        emp_history_trimmed = [
            {
                "title": e.get("title"),
                "organizationName": e.get("organization_name"),
                "organizationId": e.get("organization_id"),
                "startDate": e.get("start_date"),
                "endDate": e.get("end_date"),
                "current": bool(e.get("current")),
            }
            for e in (person.get("employment_history") or [])
        ]
        org_slim = {
            "name": org.get("name"),
            "primaryDomain": org.get("primary_domain"),
            "industry": org.get("industry"),
            "estimatedNumEmployees": org.get("estimated_num_employees"),
            "foundedYear": org.get("founded_year"),
            "hqCity": org.get("city"),
            "hqCountry": org.get("country"),
            "shortDescription": org.get("short_description"),
            "logoUrl": org.get("logo_url"),
            "linkedinUrl": org.get("linkedin_url"),
            "websiteUrl": org.get("website_url"),
        }
        enriched_data = {
            "email": person.get("email"),
            "emailStatus": person.get("email_status"),
            "personalEmails": person.get("personal_emails") or [],
            "linkedinUrl": person.get("linkedin_url"),
            "photoUrl": person.get("photo_url"),
            "title": person.get("title"),
            "headline": person.get("headline"),
            "seniority": person.get("seniority"),
            "functions": person.get("functions") or [],
            "departments": person.get("departments") or [],
            "location": person.get("formatted_address"),
            "timeZone": person.get("time_zone"),
            "employmentHistory": emp_history_trimmed,
            "socials": {
                "twitter": person.get("twitter_url"),
                "github": person.get("github_url"),
                "facebook": person.get("facebook_url"),
            },
            "organization": org_slim,
        }

        # ── Hydrate top-level fields with the authoritative data ────────────
        # The free /mixed_people/api_search call only gives first_name (last
        # name stays "Unknown") and a sparse location. Now that we have the
        # full /people/match response, fill those in so the table view shows
        # the real name + city/country without having to drill into the slideout.
        top_level: Dict[str, Any] = {
            "isEnriched": True,
            "enrichedAt": now,
            "enrichedData": enriched_data,
            # Full audit payload — never trimmed.
            "enrichedRaw": raw_envelope,
            "enrichedSource": "apollo:/people/match",
            "updatedAt": now,
        }
        if person.get("first_name"):
            top_level["firstName"] = person["first_name"]
        if person.get("last_name"):
            top_level["lastName"] = person["last_name"]
        if person.get("name"):
            top_level["displayName"] = person["name"]
        if person.get("formatted_address"):
            top_level["location"] = person["formatted_address"]
        if person.get("title"):
            top_level["currentTitle"] = person["title"]
        if person.get("headline"):
            top_level["headline"] = person["headline"]
        if person.get("linkedin_url"):
            top_level["externalLinkedinUrl"] = person["linkedin_url"]
        if org.get("name"):
            top_level["currentCompany"] = org["name"]
        if org.get("primary_domain"):
            top_level["currentCompanyDomain"] = org["primary_domain"]

        await col.update_one({"_id": oid}, {"$set": top_level})
        out = await col.find_one({"_id": oid})
        return _serialize_candidate(out)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error enriching candidate: {e}")
