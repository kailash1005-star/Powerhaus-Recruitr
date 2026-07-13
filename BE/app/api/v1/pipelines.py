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

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.database import get_database
from app.services.candidate_pipeline import (
    add_job_to_pipeline,
    enqueue_job_discover,
    enqueue_job_enrich,
    rerun_job_search,
)

logger = logging.getLogger(__name__)

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


class BulkEnrichSchema(BaseModel):
    """Selected candidate ids to enrich (None/empty → all candidates in the job)."""
    candidateIds: Optional[List[str]] = None


class JobMatchSchema(BaseModel):
    """Selected candidate ids to match against the job's JD."""
    candidateIds: Optional[List[str]] = None
    returnTop: Optional[int] = None


class DiscoverSchema(BaseModel):
    """Apify LinkedIn-search filters from the discovery questionnaire.

    All fields optional; list filters are arrays, enum filters single strings.
    Unknown extra keys are passed through to the actor mapping.
    """
    model_config = {"extra": "allow"}
    searchQuery: Optional[str] = None
    maxItems: Optional[int] = 25
    locations: Optional[List[str]] = None
    currentJobTitles: Optional[List[str]] = None
    pastJobTitles: Optional[List[str]] = None
    currentCompanies: Optional[List[str]] = None
    pastCompanies: Optional[List[str]] = None
    schools: Optional[List[str]] = None
    industryIds: Optional[List[str]] = None
    firstNames: Optional[List[str]] = None
    lastNames: Optional[List[str]] = None
    companyHqLocations: Optional[List[str]] = None
    excludeLocations: Optional[List[str]] = None
    excludeCurrentCompanies: Optional[List[str]] = None
    excludeCurrentJobTitles: Optional[List[str]] = None
    yearsOfExperience: Optional[str] = None
    yearsAtCurrentCompany: Optional[str] = None
    seniorityLevel: Optional[str] = None
    function: Optional[str] = None
    companyHeadcount: Optional[str] = None
    profileLanguage: Optional[str] = None
    recentlyChangedJobs: Optional[bool] = None
    recentlyPostedOnLinkedin: Optional[bool] = None


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


# ── POST /{id}/jobs/{jobId}/discover (Apify search questionnaire → enrich) ────


@router.post("/{pipeline_id}/jobs/{job_id}/discover")
async def discover_job_candidates(pipeline_id: str, job_id: str, body: DiscoverSchema):
    """Run the Apify LinkedIn-search actor with the questionnaire filters, store
    the results as candidates, then auto-enrich each via the profile scraper —
    all in the background. Poll the job's ``searchStatus`` then ``enrichStatus``."""
    try:
        filters = body.model_dump(exclude_none=True)
        max_items = int(filters.pop("maxItems", 25) or 25)
        result = await enqueue_job_discover(pipeline_id, job_id, filters, max_items)
        return {"success": True, **result}
    except ValueError as ve:
        raise HTTPException(404, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Error starting discovery: {e}")


# ── POST /{id}/jobs/{jobId}/enrich (background Apollo→Apify bulk enrich) ──────


@router.post("/{pipeline_id}/jobs/{job_id}/enrich")
async def enrich_job_candidates(pipeline_id: str, job_id: str, body: BulkEnrichSchema):
    """Queue a background bulk enrichment (Apollo /people/match → Apify profile)
    for the selected candidates (or all candidates in the job). Idempotent — each
    stage skips candidates already enriched. Poll the pipeline for the job's
    ``enrichStatus``."""
    try:
        result = await enqueue_job_enrich(pipeline_id, job_id, body.candidateIds)
        return {"success": True, **result}
    except ValueError as ve:
        raise HTTPException(404, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Error queuing enrichment: {e}")


# ── POST /{id}/jobs/{jobId}/match (background JD ↔ enriched-candidate match) ──


@router.post("/{pipeline_id}/jobs/{job_id}/match")
async def match_job_candidates(pipeline_id: str, job_id: str, body: JobMatchSchema):
    """Start a background match run: score the job's JD against the selected
    candidates' enriched profiles (auto-enriching any that aren't yet). Returns
    a ``matchRunId`` to poll at ``GET /matching/run/{id}``."""
    from app.services.pipeline_match_service import start_pipeline_match
    try:
        match_run_id = await start_pipeline_match(
            pipeline_id=pipeline_id,
            job_id=job_id,
            candidate_ids=body.candidateIds,
            return_top=body.returnTop,
        )
        return {"success": True, "matchRunId": match_run_id}
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Error starting match: {e}")


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


# ── GET /candidates/{id} (single — deep profile / enrich poll) ────────────


@router.get("/candidates/{candidate_id}")
async def get_candidate(candidate_id: str, db=Depends(get_db)):
    """Fetch one candidate (full doc incl. Apollo + Apify enrichment).

    Backs the matching-run slide-out (deep Apify profile) and the poll the
    client runs after the manual enrich button to watch the Apify stage settle.
    """
    try:
        col = db["candidates"]
        try:
            oid = ObjectId(candidate_id)
        except Exception:
            raise HTTPException(400, "Invalid candidate id")
        doc = await col.find_one({"_id": oid})
        if not doc:
            raise HTTPException(404, "Candidate not found")
        return _serialize_candidate(doc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error fetching candidate: {e}")


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


async def _bg_apify_enrich(candidate_id: str) -> None:
    """Background stage-2: pull the deep Apify LinkedIn profile for one candidate
    after the Apollo stage. Detached from the request (fire-and-forget), so it
    records a TERMINAL ``apifyEnrichmentStatus`` the client can stop polling on:

      • ``enriched`` / ``not_found`` — set by ``enrich_candidates``.
      • ``not_found``               — candidate had no usable LinkedIn URL, so
                                       ``enrich_candidates`` skipped it silently;
                                       normalize here so the UI doesn't hang.
      • ``failed``                  — the Apify actor call itself errored.
    """
    from app.services.candidate_enrichment import enrich_candidates
    from app.services import cost_service
    db = await get_database()
    col = db["candidates"]
    oid = ObjectId(candidate_id)
    ref = await col.find_one({"_id": oid}, {"pipelineId": 1, "sourceJobIds": 1, "displayName": 1})
    ref = ref or {}
    try:
        async with cost_service.cost_context(
            cost_service.STAGE_CANDIDATE, label=ref.get("displayName"),
            candidateId=candidate_id, pipelineId=ref.get("pipelineId"),
            jobId=(ref.get("sourceJobIds") or [None])[0],
        ):
            await enrich_candidates(candidate_ids=[candidate_id])
        doc = await col.find_one({"_id": oid}, {"apifyEnrichmentStatus": 1})
        if (doc or {}).get("apifyEnrichmentStatus") == "pending":
            await col.update_one(
                {"_id": oid},
                {"$set": {"apifyEnrichmentStatus": "not_found", "updatedAt": datetime.utcnow()}},
            )
    except Exception as e:  # noqa: BLE001 — a detached task must never crash the loop
        logger.warning("[Enrich] background Apify stage failed for %s: %s", candidate_id, e)
        try:
            await col.update_one(
                {"_id": oid},
                {"$set": {
                    "apifyEnrichmentStatus": "failed",
                    "apifyEnrichmentError": str(e)[:300],
                    "updatedAt": datetime.utcnow(),
                }},
            )
        except Exception:  # noqa: BLE001
            pass


@router.post("/candidates/{candidate_id}/enrich")
async def enrich_candidate(candidate_id: str, db=Depends(get_db)):
    """Manual enrichment — Apollo /people/match (inline) then the deep Apify
    LinkedIn profile (background).

    Apollo runs synchronously (fast) and hydrates the verified email + the
    authoritative LinkedIn URL Apify needs; the response returns immediately with
    ``apifyEnrichmentStatus:"pending"`` and the Apify stage continues in the
    background. The client polls ``GET /candidates/{id}`` until the status
    settles (``enriched`` / ``not_found`` / ``failed``). Idempotent.
    """
    from app.services.apollo_enrich import ApolloEnrichError, apollo_enrich_candidate
    try:
        col = db["candidates"]
        try:
            oid = ObjectId(candidate_id)
        except Exception:
            raise HTTPException(400, "Invalid candidate id")
        cand = await col.find_one({"_id": oid})
        if not cand:
            raise HTTPException(404, "Candidate not found")

        from app.services import cost_service
        async with cost_service.cost_context(
            cost_service.STAGE_CANDIDATE, label=cand.get("displayName"),
            candidateId=candidate_id, pipelineId=cand.get("pipelineId"),
            jobId=(cand.get("sourceJobIds") or [None])[0],
        ):
            fresh = await apollo_enrich_candidate(db, cand)

        # Kick off stage-2 (Apify) unless the deep profile is already present.
        if not fresh.get("isApifyEnriched"):
            await col.update_one(
                {"_id": oid},
                {"$set": {"apifyEnrichmentStatus": "pending", "updatedAt": datetime.utcnow()}},
            )
            fresh["apifyEnrichmentStatus"] = "pending"
            asyncio.create_task(_bg_apify_enrich(candidate_id))

        return _serialize_candidate(fresh)
    except ApolloEnrichError as e:
        raise HTTPException(502, str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error enriching candidate: {e}")
