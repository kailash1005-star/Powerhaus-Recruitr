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
  POST   /pipelines/{id}/jobs/{jobId}/suggest-filters — AI-proposed search filters
  GET    /pipelines/{id}/jobs/{jobId}/candidates   — paginated candidates for a job

  PATCH  /candidates/{id}                          — accept/reject (+ reason)
  POST   /candidates/{id}/enrich                   — Apollo /people/match enrichment
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.database import get_database
from app.services.candidate_pipeline import (
    add_job_to_pipeline,
    enqueue_apollo_discover,
    enqueue_combined_discover,
    enqueue_job_discover,
    enqueue_job_enrich,
    recount_pipeline,
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
    """Selected candidate ids to enrich (None/empty → all candidates in the job).

    ``mode`` picks the enrichment engine(s): ``"apollo"`` (verified email +
    contact, no scrape), ``"apify"`` (deep profile scrape only), or ``"both"``
    (Apollo then Apify — the default).
    """
    candidateIds: Optional[List[str]] = None
    mode: Literal["apollo", "apify", "both"] = "both"


class DeleteCandidatesSchema(BaseModel):
    """Selected candidate ids to remove from a job."""
    candidateIds: List[str]


class JobMatchSchema(BaseModel):
    """Selected candidate ids to match against the job's JD.

    The three requirement fields carry the recruiter's edits from the
    review-before-match step. None = keep the parsed value; an empty list is a
    deliberate edit ("no must-haves") and is honoured as such.
    """
    candidateIds: Optional[List[str]] = None
    returnTop: Optional[int] = None
    mustHaveSkills: Optional[List[str]] = None
    niceToHaveSkills: Optional[List[str]] = None
    minYears: Optional[float] = None


class SuggestFiltersSchema(BaseModel):
    """The recruiter's optional brief for the Strategist.

    Every field is a hint, not a requirement — the job title, location, company
    and JD are read from the database. Filling these in sharpens the proposal.
    """
    seniorityHint: Optional[str] = None
    mustHaveSkills: Optional[List[str]] = None
    niceToHaveSkills: Optional[List[str]] = None
    minYears: Optional[float] = None
    targetIndustries: Optional[List[str]] = None
    targetCompanies: Optional[List[str]] = None
    excludeCompanies: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    workModel: Optional[str] = None
    openToRelocation: Optional[bool] = None
    notes: Optional[str] = None


class DiscoverSchema(BaseModel):
    """Apify LinkedIn-search filters from the discovery questionnaire.

    All fields optional; list filters are arrays, enum filters single strings.
    Unknown extra keys are passed through to the actor mapping.
    """
    model_config = {"extra": "allow"}
    searchQuery: Optional[str] = None
    maxItems: Optional[int] = 25
    # ── Agentic search (not actor filters — stripped before the actor call) ──
    # When true, a zero-result search is retried with agent-broadened filters
    # instead of returning an empty list.
    autoBroaden: Optional[bool] = True
    # The brief from suggest-filters, so the Broadener knows the role's intent.
    brief: Optional[SuggestFiltersSchema] = None
    # The Strategist's pre-planned fallbacks, echoed back from suggest-filters.
    broadeningLadder: Optional[List[Dict[str, Any]]] = None
    # The Strategist's two-tier domain anchor ({coreTerms, ecosystemTerms}),
    # echoed back from suggest-filters. Guards broadening against changing the
    # target profession.
    domainAnchor: Optional[Dict[str, Any]] = None
    # Adjacent-specialty titles from the Strategist — NEVER searched
    # automatically; offered as opt-in chips when the search comes up short.
    adjacentTitles: Optional[List[str]] = None
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
    # The actor key is PLURAL. The singular field stayed accepted for
    # back-compat, but it used to be silently dropped by the filter mapping —
    # a recruiter's language choice never reached the search. Both now work
    # (normalized in apify_search_service._build_input).
    profileLanguage: Optional[str] = None
    profileLanguages: Optional[List[str]] = None
    recentlyChangedJobs: Optional[bool] = None
    recentlyPostedOnLinkedin: Optional[bool] = None


class ApolloDiscoverSchema(BaseModel):
    """Apollo people-search filters from the Apollo discovery questionnaire.

    A separate, simpler engine from the Apify LinkedIn scrape: all fields ANDed
    into one Apollo people-search. ``skills`` has no structured Apollo filter, so
    it is matched as free-text ``q_keywords`` (a soft match). Results are
    search-only — no auto-enrichment, no Apify — so contact info stays masked
    until the recruiter reveals it on demand.
    """
    titles: Optional[List[str]] = None
    locations: Optional[List[str]] = None
    # Key skills → Apollo q_keywords (free-text, soft match across the profile).
    skills: Optional[List[str]] = None
    # Apollo person_seniorities enum codes (owner, c_suite, vp, head, director,
    # manager, senior, entry, intern, partner, founder).
    seniorities: Optional[List[str]] = None
    industries: Optional[List[str]] = None
    maxItems: Optional[int] = 25


class EngineToggles(BaseModel):
    """Which engines the unified 'Run search' fires. Both default on."""
    apify: bool = True
    apollo: bool = True


class CombinedDiscoverSchema(BaseModel):
    """The unified discovery payload — one screen, both engines.

    ``apify`` carries the LinkedIn actor filters (same shape as ``DiscoverSchema``,
    including the agentic controls the loop strips) and ``apollo`` the Apollo
    people-search filters. ``engines`` toggles either off before running. The
    shared ``maxItems`` comes from the Apify block.
    """
    apify: DiscoverSchema = Field(default_factory=DiscoverSchema)
    apollo: ApolloDiscoverSchema = Field(default_factory=ApolloDiscoverSchema)
    engines: EngineToggles = Field(default_factory=EngineToggles)


# ── Helpers ──────────────────────────────────────────────────────────────


def _serialize_pipeline(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    if doc.get("companyId") and not isinstance(doc["companyId"], str):
        doc["companyId"] = str(doc["companyId"])
    return doc


def _serialize_candidate(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    # The avatar-sized variant. `photoUrl` stays as stored (the 800px crop) so the
    # existing contract is unchanged; this is the one a 40px circle should load.
    from app.services import profile_photo

    doc["photoThumbUrl"] = profile_photo.pick(doc, min_px=200)
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

        # 4. Roll up pipeline totals + the surviving jobs' counts (step 3 detached
        #    this job from candidates that other jobs also surfaced).
        await recount_pipeline(pipeline_id)
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


@router.post("/{pipeline_id}/jobs/{job_id}/suggest-filters")
async def suggest_job_filters(
    pipeline_id: str, job_id: str, body: SuggestFiltersSchema | None = None,
):
    """AI-propose the LinkedIn search filters for a job (prefill).

    Reads the job title, JD and company from the database, layers on the
    recruiter's optional brief, and returns a ``SearchStrategy``: the filters
    real profiles actually match, why each was chosen, a broadening ladder, and a
    confidence score.

    Reasoning only — one LLM call, no vendor spend, no candidates sourced. Never
    fails the request: with no LLM key (or on an agent error) it returns the
    literal job-title prefill with ``confidence: 0`` and a warning.
    """
    from app.services.sourcing import build_brief, propose_strategy

    try:
        hints = body.model_dump(exclude_none=True) if body else {}
        brief = await build_brief(pipeline_id, job_id, hints)
    except ValueError as ve:
        raise HTTPException(404, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Error building the search brief: {e}")

    strategy = await propose_strategy(brief)
    return {"success": True, "strategy": strategy.model_dump(mode="json")}


@router.post("/{pipeline_id}/jobs/{job_id}/discover")
async def discover_job_candidates(pipeline_id: str, job_id: str, body: DiscoverSchema):
    """Run the Apify LinkedIn-search actor with the questionnaire filters, store
    the results as candidates, then auto-enrich each via the profile scraper —
    all in the background. Poll the job's ``searchStatus`` then ``enrichStatus``.

    When ``autoBroaden`` is set, a zero-result search doesn't just report zero:
    the Broadener agent relaxes the filters and retries (bounded by
    ``SOURCING_MAX_BROADEN_ATTEMPTS``). Every attempt is recorded on the job's
    ``searchAttempts`` for the UI to show.
    """
    try:
        payload = body.model_dump(exclude_none=True)
        # Strip the agentic controls — everything left is an actor filter.
        max_items = int(payload.pop("maxItems", 25) or 25)
        auto_broaden = bool(payload.pop("autoBroaden", True))
        hints = payload.pop("brief", None)
        ladder = payload.pop("broadeningLadder", None)
        anchor = payload.pop("domainAnchor", None)
        adjacent = payload.pop("adjacentTitles", None)
        result = await enqueue_job_discover(
            pipeline_id, job_id, payload, max_items,
            auto_broaden=auto_broaden, hints=hints, ladder=ladder,
            anchor=anchor, adjacent_titles=adjacent,
        )
        return {"success": True, **result}
    except ValueError as ve:
        raise HTTPException(404, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Error starting discovery: {e}")


@router.post("/{pipeline_id}/jobs/{job_id}/discover-apollo")
async def discover_job_candidates_apollo(
    pipeline_id: str, job_id: str, body: ApolloDiscoverSchema,
):
    """Run an Apollo people-search from the Apollo questionnaire filters, store
    the results as candidates (search-only), all in the background. Poll the
    job's ``searchStatus``.

    This is the Apollo alternative to ``/discover``: no LinkedIn scrape and no
    auto-enrichment. Key skills are matched via Apollo ``q_keywords`` (soft
    match). Contact info stays masked until revealed on demand.
    """
    try:
        payload = body.model_dump(exclude_none=True)
        max_items = int(payload.pop("maxItems", 25) or 25)
        result = await enqueue_apollo_discover(pipeline_id, job_id, payload, max_items)
        return {"success": True, **result}
    except ValueError as ve:
        raise HTTPException(404, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Error starting Apollo discovery: {e}")


@router.post("/{pipeline_id}/jobs/{job_id}/discover-combined")
async def discover_job_candidates_combined(
    pipeline_id: str, job_id: str, body: CombinedDiscoverSchema,
):
    """Run the Apify LinkedIn search AND the Apollo people-search CONCURRENTLY
    from one payload, store both engines' results as candidates (deduped by
    LinkedIn URL), all in the background. Poll the job's rollup ``searchStatus``,
    plus ``apifySearchStatus`` / ``apolloSearchStatus`` (+ ``apifyKept`` /
    ``apolloKept``) for the per-engine breakdown, then ``enrichStatus``.

    Either engine can be switched off via ``engines`` (e.g. Apollo-only spends no
    Apify credit). The Apify block is stripped of its agentic controls exactly as
    ``/discover`` does; everything left is an actor filter.
    """
    try:
        apify_payload = body.apify.model_dump(exclude_none=True)
        max_items = int(apify_payload.pop("maxItems", 25) or 25)
        apify_payload.pop("autoBroaden", None)
        hints = apify_payload.pop("brief", None)
        ladder = apify_payload.pop("broadeningLadder", None)
        anchor = apify_payload.pop("domainAnchor", None)
        adjacent = apify_payload.pop("adjacentTitles", None)

        apollo_payload = body.apollo.model_dump(exclude_none=True)
        apollo_payload.pop("maxItems", None)  # shared budget from the Apify block

        engines = {"apify": body.engines.apify, "apollo": body.engines.apollo}
        result = await enqueue_combined_discover(
            pipeline_id, job_id, apify_payload, apollo_payload, engines, max_items,
            hints=hints, ladder=ladder, anchor=anchor, adjacent_titles=adjacent,
        )
        return {"success": True, **result}
    except ValueError as ve:
        raise HTTPException(404, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Error starting combined discovery: {e}")


# ── POST /{id}/jobs/{jobId}/enrich (background Apollo→Apify bulk enrich) ──────


@router.post("/{pipeline_id}/jobs/{job_id}/enrich")
async def enrich_job_candidates(pipeline_id: str, job_id: str, body: BulkEnrichSchema):
    """Queue a background bulk enrichment (Apollo /people/match → Apify profile)
    for the selected candidates (or all candidates in the job). Idempotent — each
    stage skips candidates already enriched. Poll the pipeline for the job's
    ``enrichStatus``.

    Selections are capped at ``JOB_ENRICH_SELECTION_MAX`` (default 10): one
    enrichment click = one Apify actor run, and the free-tier run budget is the
    scarce resource, not the dollars. The cap is enforced here — not only in the
    UI — so no client can silently burn the budget.
    """
    from app.config import settings

    cap = max(1, int(settings.JOB_ENRICH_SELECTION_MAX))
    if body.candidateIds is not None and len(body.candidateIds) > cap:
        raise HTTPException(
            400,
            f"Enrichment is capped at {cap} candidates per request — you sent "
            f"{len(body.candidateIds)}. Pick your {cap} strongest candidates; "
            f"you can always enrich more in a second batch.",
        )
    try:
        result = await enqueue_job_enrich(
            pipeline_id, job_id, body.candidateIds, body.mode)
        return {"success": True, **result}
    except ValueError as ve:
        raise HTTPException(404, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Error queuing enrichment: {e}")


# ── POST /{id}/jobs/{jobId}/candidates/delete (remove selected candidates) ────


@router.post("/{pipeline_id}/jobs/{job_id}/candidates/delete")
async def delete_job_candidates(
    pipeline_id: str, job_id: str, body: DeleteCandidatesSchema, db=Depends(get_db),
):
    """Delete the selected candidates from a job.

    Mirrors the job-removal cascade: a candidate surfaced ONLY by this job is
    deleted outright; one also surfaced by other jobs is kept, just detached from
    this job (its ``sourceJobIds`` is trimmed). Pipeline/job counts are then
    recomputed. Scoped to ``pipelineId`` so a stray id can't touch another
    pipeline's candidates.
    """
    if not body.candidateIds:
        raise HTTPException(400, "no candidate ids provided")
    try:
        oids = [ObjectId(c) for c in body.candidateIds]
    except Exception:
        raise HTTPException(400, "invalid candidate id")
    try:
        candidates = db["candidates"]
        now = datetime.utcnow()
        scope = {"pipelineId": pipeline_id, "_id": {"$in": oids}}
        # 1. Candidates uniquely tied to this job → delete outright.
        deleted = await candidates.delete_many({**scope, "sourceJobIds": [job_id]})
        # 2. Shared with other jobs → detach this job only (keep the doc).
        await candidates.update_many(
            {**scope, "sourceJobIds": job_id},
            {"$pull": {"sourceJobIds": job_id}, "$set": {"updatedAt": now}},
        )
        await recount_pipeline(pipeline_id)
        return {"success": True, "deleted": deleted.deleted_count}
    except Exception as e:
        raise HTTPException(500, f"Error deleting candidates: {e}")


# ── POST /{id}/jobs/{jobId}/match (background JD ↔ enriched-candidate match) ──


@router.post("/{pipeline_id}/jobs/{job_id}/match")
async def match_job_candidates(pipeline_id: str, job_id: str, body: JobMatchSchema):
    """Start a background match run: score the job's JD against the selected
    candidates' enriched profiles (auto-enriching any that aren't yet). Returns
    a ``matchRunId`` to poll at ``GET /matching/run/{id}``."""
    from app.services.pipeline_match_service import start_pipeline_match
    try:
        # Only forward requirement fields the recruiter actually sent — None
        # means "keep the parsed value", an empty list is a deliberate edit.
        override = {
            k: v for k, v in (
                ("mustHaveSkills", body.mustHaveSkills),
                ("niceToHaveSkills", body.niceToHaveSkills),
                ("minYears", body.minYears),
            ) if v is not None
        }
        match_run_id = await start_pipeline_match(
            pipeline_id=pipeline_id,
            job_id=job_id,
            candidate_ids=body.candidateIds,
            return_top=body.returnTop,
            requirements_override=override or None,
        )
        return {"success": True, "matchRunId": match_run_id}
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Error starting match: {e}")


# ── GET /{id}/jobs/{jobId}/requirements (review-before-match) ────────────────


@router.get("/{pipeline_id}/jobs/{job_id}/requirements")
async def get_job_requirements(pipeline_id: str, job_id: str):
    """The parsed hiring requirements for a job — what a match run will score
    against. Parses the JD on first ask (cached by JD-text hash afterwards).

    This powers the review step before Run Match: the recruiter sees the
    extracted must-have / nice-to-have skills, adjusts them, and the match runs
    with their corrected list. 404 when the job has no description or title to
    parse.
    """
    from app.database import get_database
    from app.services import role_spec_service
    from app.services.llm_extraction_service import ExtractionError

    try:
        db = await get_database()
        spec = await role_spec_service.get_or_create_for_job(db, job_id)
        if not spec:
            raise HTTPException(404, "job has no description or title to parse")
        req = spec.get("requirements") or {}
        return {
            "success": True,
            "jdId": spec["_id"],
            "requirements": {
                "title": req.get("title"),
                "mustHaveSkills": req.get("mustHaveSkills") or [],
                "niceToHaveSkills": req.get("niceToHaveSkills") or [],
                "minYears": req.get("minYears"),
                "location": req.get("location"),
                "seniority": req.get("seniority"),
            },
            "requirementsSource": spec.get("requirementsSource") or "parsed",
        }
    except HTTPException:
        raise
    except ExtractionError as e:
        raise HTTPException(502, f"JD parsing failed: {e}")
    except Exception as e:
        raise HTTPException(500, f"Error resolving requirements: {e}")


# ── GET /{id}/jobs/{jobId}/candidates ────────────────────────────────────


# ── Column filtering ──────────────────────────────────────────────────────
# The candidates table filters per column, combined with AND across columns and
# OR within one column (checking Infosys + BearingPoint means "either"). Applied
# server-side because the table is paginated: filtering the fetched page only
# would give wrong totals and hide matches on other pages.

# Filter key → the candidate field it targets. Used by both the list query and
# the facet aggregation, so the two can never disagree about what a filter means.
_FACET_FIELDS = {"companies": "currentCompany", "locations": "location", "status": "isAccepted"}


def _candidate_query(
    pipeline_id: str, job_id: str, *,
    name: Optional[str] = None,
    role: Optional[str] = None,
    companies: Optional[List[str]] = None,
    locations: Optional[List[str]] = None,
    status: Optional[List[str]] = None,
    match_min: Optional[int] = None,
    match_max: Optional[int] = None,
    exclude: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the Mongo query for the candidates table.

    `exclude` drops one filter key from the query. The facet aggregation uses it
    so each column's option counts reflect the OTHER columns' filters but not its
    own — otherwise ticking one company would collapse that column's own list to
    a single option, and you could never widen the selection.
    """
    query: Dict[str, Any] = {"pipelineId": pipeline_id, "sourceJobIds": job_id}

    # Free text: case-insensitive "contains". The value is escaped — an
    # unescaped user string is a regex, so a stray "(" would 500 the endpoint.
    if name and exclude != "name":
        query["displayName"] = {"$regex": re.escape(name.strip()), "$options": "i"}
    if role and exclude != "role":
        query["currentTitle"] = {"$regex": re.escape(role.strip()), "$options": "i"}

    if companies and exclude != "companies":
        query["currentCompany"] = {"$in": companies}
    if locations and exclude != "locations":
        query["location"] = {"$in": locations}

    if status and exclude != "status":
        # Both ticked is the same as no filter — don't emit $in:[true,false],
        # which would wrongly drop candidates that have no decision yet.
        wanted = {s.lower() for s in status}
        if wanted == {"accepted"}:
            query["isAccepted"] = True
        elif wanted == {"rejected"}:
            query["isAccepted"] = False

    if exclude != "match":
        bounds = {}
        if match_min is not None:
            bounds["$gte"] = match_min
        if match_max is not None:
            bounds["$lte"] = match_max
        if bounds:
            query["matchScore"] = bounds

    return query


@router.get("/{pipeline_id}/jobs/{job_id}/candidates/facets")
async def job_candidate_facets(
    pipeline_id: str,
    job_id: str,
    name: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    companies: Optional[List[str]] = Query(None),
    locations: Optional[List[str]] = Query(None),
    status: Optional[List[str]] = Query(None),
    match_min: Optional[int] = Query(None),
    match_max: Optional[int] = Query(None),
    db=Depends(get_db),
):
    """Distinct values + counts for each filterable column, honouring the other
    columns' active filters. Powers the checkboxes in the header dropdowns.

    One `$facet` aggregation: each branch re-filters the job's candidates with
    every filter except its own column's.
    """
    try:
        col = db["candidates"]
        args = dict(name=name, role=role, companies=companies, locations=locations,
                    status=status, match_min=match_min, match_max=match_max)

        def branch(key: str, field: str) -> List[Dict[str, Any]]:
            # $facet branches all start from the same input, so each re-applies
            # its own $match rather than sharing an earlier one.
            return [
                {"$match": _candidate_query(pipeline_id, job_id, exclude=key, **args)},
                {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
                {"$sort": {"count": -1, "_id": 1}},
                {"$limit": 500},
            ]

        pipeline = [
            {"$match": {"pipelineId": pipeline_id, "sourceJobIds": job_id}},
            {"$facet": {key: branch(key, field) for key, field in _FACET_FIELDS.items()}},
        ]
        result = await col.aggregate(pipeline).to_list(1)
        raw = result[0] if result else {}

        def values(key: str) -> List[Dict[str, Any]]:
            out = []
            for b in raw.get(key, []):
                v = b["_id"]
                # Blank company/location carries no meaning as a filter option.
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                out.append({"value": v, "count": b["count"]})
            return out

        return {
            "companies": values("companies"),
            "locations": values("locations"),
            # isAccepted groups to true/false — relabel for the UI.
            "status": [
                {"value": "accepted" if b["_id"] else "rejected", "count": b["count"]}
                for b in raw.get("status", []) if b["_id"] is not None
            ],
        }
    except Exception as e:
        raise HTTPException(500, f"Error building candidate facets: {e}")


@router.get("/{pipeline_id}/jobs/{job_id}/candidates")
async def list_job_candidates(
    pipeline_id: str,
    job_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    name: Optional[str] = Query(None, description="Candidate name contains"),
    role: Optional[str] = Query(None, description="Current role contains"),
    companies: Optional[List[str]] = Query(None, description="Current company is any of"),
    locations: Optional[List[str]] = Query(None, description="Location is any of"),
    status: Optional[List[str]] = Query(None, description="accepted and/or rejected"),
    match_min: Optional[int] = Query(None, ge=0, le=100),
    match_max: Optional[int] = Query(None, ge=0, le=100),
    sort_by: str = Query("matchScore", description="matchScore|createdAt"),
    sort_order: str = Query("desc"),
    db=Depends(get_db),
):
    try:
        col = db["candidates"]
        query = _candidate_query(
            pipeline_id, job_id, name=name, role=role, companies=companies,
            locations=locations, status=status, match_min=match_min, match_max=match_max,
        )
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

        # Roll up pipeline totals + per-job counts. This used to update only the
        # pipeline, leaving the per-job accepted/rejected stale after a decision.
        pipeline_id = cand.get("pipelineId")
        if pipeline_id:
            await recount_pipeline(pipeline_id)

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
