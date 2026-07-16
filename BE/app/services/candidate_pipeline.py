"""
Candidate Pipeline Orchestrator — "Phase 4"

A pipeline is one company + multiple jobs. Adding a job kicks off a per-job
background Apollo people-search that lands candidates in the ``candidates``
collection. The state machine on each embedded ``jobs[]`` entry guarantees
exactly-once execution per (pipeline, job):

      ┌───────┐  add_job_to_pipeline  ┌────────┐  worker picks it up  ┌─────────┐
      │  ∅    │ ────────────────────▶│ queued │ ─────────────────────▶│ running │
      └───────┘                       └────────┘                       └─────────┘
                                                                            │
                                            ┌───────────────────────────────┤
                                            ▼                               ▼
                                     ┌──────────┐                    ┌──────────┐
                                     │completed │                    │  failed  │
                                     └──────────┘                    └──────────┘
                                            ▲                               │
                                            └──────────  rerun  ────────────┘

The state transitions are done with conditional ``update_one`` queries — if a
parallel worker already grabbed the job, the second worker bails silently.
Multiple pipelines / multiple jobs across pipelines run in true parallel; the
only lock is per-(pipeline, job).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.database import get_collection
from app.services.apollo_service import ApolloService
from app.services.location_resolver import resolve_search_country

logger = logging.getLogger(__name__)


# ── normalization helpers ──────────────────────────────────────────────────

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(s: Optional[str]) -> str:
    """Lowercase + strip + collapse non-alphanumerics for fuzzy company match."""
    if not s:
        return ""
    return _NON_ALNUM.sub("", s.lower().strip())


# ── candidate doc builder ──────────────────────────────────────────────────


def _split_name(person: dict) -> Tuple[str, str]:
    first = (person.get("first_name") or "").strip()
    last = (person.get("last_name") or "").strip()
    if first or last:
        return first or "Unknown", last or "Unknown"
    full = (person.get("name") or "").strip()
    if not full:
        return "Unknown", "Unknown"
    parts = full.split(" ", 1)
    return parts[0], parts[1] if len(parts) > 1 else "Unknown"


def _build_candidate_doc(
    person: dict,
    *,
    pipeline_id: str,
    job_id: str,
    applied_industry_fallback: bool,
    match_score: int,
    match_reasons: List[str],
    now: datetime,
) -> dict:
    first, last = _split_name(person)
    org = person.get("organization") or {}
    return {
        "pipelineId": pipeline_id,
        "sourceJobIds": [job_id],
        "apolloId": person.get("id") or "",
        "externalLinkedinUrl": person.get("linkedin_url") or "",
        "firstName": first,
        "lastName": last,
        "displayName": person.get("name") or f"{first} {last}".strip(),
        "headline": person.get("headline") or "",
        "currentTitle": person.get("title") or "",
        "currentCompany": org.get("name") or "",
        "currentCompanyDomain": org.get("primary_domain") or org.get("website_url") or "",
        "location": ", ".join(
            [p for p in (person.get("city"), person.get("state"), person.get("country")) if p]
        ),
        "matchScore": match_score,
        "matchReasons": match_reasons,
        "isAccepted": True,
        "rejectionReason": None,
        "decidedAt": None,
        "isEnriched": False,
        "enrichedAt": None,
        "enrichedData": None,
        "runHistory": [
            {
                "runAt": now,
                "jobId": job_id,
                "isRerun": False,
                "appliedIndustryFallback": applied_industry_fallback,
            }
        ],
        "createdAt": now,
        "updatedAt": now,
    }


# ── scoring (cheap, headline-only — same approach as prospect filters) ─────

_SENIOR_RE = re.compile(r"\b(head|director|vp|vice president|chief|c[a-z]o|managing)\b", re.I)
_MANAGER_RE = re.compile(r"\bmanager\b", re.I)


def _score_match(person: dict, target_title: str, target_industry: Optional[str]) -> Tuple[int, List[str]]:
    """Cheap, transparent ranking based on Apollo search-result fields only.

    No extra API calls. Returns (score, reasons).
    """
    reasons: List[str] = []
    score = 0
    title = (person.get("title") or "").lower()
    target = (target_title or "").lower()

    # Token overlap between target title and candidate title
    target_tokens = {t for t in re.split(r"\W+", target) if len(t) > 2}
    title_tokens = {t for t in re.split(r"\W+", title) if len(t) > 2}
    overlap = target_tokens & title_tokens
    if target_tokens:
        overlap_ratio = len(overlap) / len(target_tokens)
        if overlap_ratio >= 0.8:
            score += 50
            reasons.append("title_exact_match")
        elif overlap_ratio >= 0.5:
            score += 30
            reasons.append("title_partial_match")
        elif overlap_ratio > 0:
            score += 10
            reasons.append("title_token_overlap")

    # Seniority alignment (without explicit seniority filter — derived from title)
    if _SENIOR_RE.search(title):
        score += 20
        reasons.append("senior_title")
    elif _MANAGER_RE.search(title):
        score += 10
        reasons.append("manager_title")

    # Current-industry match (Apollo's organization.industry)
    org = person.get("organization") or {}
    org_industry = (org.get("industry") or "").lower()
    if target_industry and org_industry and target_industry.lower() in org_industry:
        score += 15
        reasons.append("industry_match")

    return score, reasons


# ── same-company exclusion ─────────────────────────────────────────────────


def _is_same_company(person: dict, pipeline_company_name: str, pipeline_company_domain: str) -> bool:
    """Drop candidates currently employed by the pipeline's company itself.

    We can't pre-filter this in Apollo (no "NOT this organization" filter), so
    we post-filter. Matches on either normalized name or domain — Apollo's
    organization.name varies in formatting; matching on both is safest.
    """
    org = person.get("organization") or {}
    target_name = _norm(pipeline_company_name)
    target_domain = _norm(pipeline_company_domain.split(".")[0] if pipeline_company_domain else "")
    if target_name and _norm(org.get("name")) == target_name:
        return True
    org_domain = org.get("primary_domain") or org.get("website_url") or ""
    if target_domain and _norm(org_domain.split(".")[0] if org_domain else "") == target_domain:
        return True
    return False


# ── public API ─────────────────────────────────────────────────────────────


async def add_job_to_pipeline(pipeline_id: str, job_id: str) -> Dict[str, Any]:
    """Add a job to a pipeline and kick off a background candidate search.

    Returns {"queued": True, "alreadyExists": False} on success, or raises a
    ``ValueError`` with one of: "pipeline_not_found", "job_not_found",
    "job_already_in_pipeline".

    The actual search runs in the background via asyncio.create_task so we
    return to the HTTP layer immediately.
    """
    pipelines_col = await get_collection("candidatePipelines")
    jobs_col = await get_collection("jobs")

    pipeline_oid = ObjectId(pipeline_id)
    pipeline = await pipelines_col.find_one({"_id": pipeline_oid})
    if not pipeline:
        raise ValueError("pipeline_not_found")

    if any((j.get("jobId") == job_id) for j in (pipeline.get("jobs") or [])):
        raise ValueError("job_already_in_pipeline")

    job = await jobs_col.find_one({"_id": ObjectId(job_id)})
    if not job:
        raise ValueError("job_not_found")

    now = datetime.utcnow()
    new_entry = {
        "jobId": job_id,
        "jobTitle": job.get("title") or "",
        "jobLocation": job.get("location") or "",
        "addedAt": now,
        # The user now drives discovery via the Apify search questionnaire, so we
        # don't auto-run a search on add — the job waits for the user's filters.
        "searchStatus": "awaiting_input",
        "lastSearchedAt": None,
        "candidateCount": 0,
        "acceptedCount": 0,
        "rejectedCount": 0,
        "appliedIndustryFallback": False,
        "searchError": None,
    }
    # Atomic: only add if no entry with this jobId already exists.
    result = await pipelines_col.update_one(
        {"_id": pipeline_oid, "jobs.jobId": {"$ne": job_id}},
        {"$push": {"jobs": new_entry}, "$set": {"updatedAt": now}},
    )
    if result.modified_count == 0:
        raise ValueError("job_already_in_pipeline")

    return {"queued": False, "awaitingInput": True}


async def rerun_job_search(pipeline_id: str, job_id: str) -> Dict[str, Any]:
    """Re-run the candidate search for a job already in a pipeline.

    Only allowed when the current searchStatus is completed or failed (we don't
    queue a second worker while one is already running). Atomic transition
    completed|failed → queued; if another caller wins, raises ``busy``.
    """
    pipelines_col = await get_collection("candidatePipelines")
    pipeline_oid = ObjectId(pipeline_id)

    now = datetime.utcnow()
    result = await pipelines_col.update_one(
        {
            "_id": pipeline_oid,
            "jobs": {
                "$elemMatch": {
                    "jobId": job_id,
                    "searchStatus": {"$in": ["completed", "failed"]},
                }
            },
        },
        {
            "$set": {
                "jobs.$.searchStatus": "queued",
                "jobs.$.searchError": None,
                "updatedAt": now,
            }
        },
    )
    if result.modified_count == 0:
        raise ValueError("busy")

    asyncio.create_task(_search_candidates_for_job(pipeline_id, job_id, is_rerun=True))
    return {"queued": True}


# ── internal: the actual background search ────────────────────────────────


async def _claim_running(pipeline_id: str, job_id: str) -> Optional[dict]:
    """Atomic queued → running. Returns the pipeline doc on success, None if
    another worker already grabbed it (or the job vanished)."""
    pipelines_col = await get_collection("candidatePipelines")
    pipeline = await pipelines_col.find_one_and_update(
        {
            "_id": ObjectId(pipeline_id),
            "jobs": {"$elemMatch": {"jobId": job_id, "searchStatus": "queued"}},
        },
        {
            "$set": {
                "jobs.$.searchStatus": "running",
                "jobs.$.lastSearchedAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return pipeline


async def recount_pipeline(pipeline_id: str) -> Dict[str, int]:
    """Recompute every denormalized count on a pipeline from the candidates.

    The `candidates` collection is the source of truth; the counts on the
    pipeline and its embedded jobs are a cache for the list UI. This recomputes
    the lot — per-job candidate/accepted/rejected, then the pipeline rollup.

    EVERY writer that adds, removes, accepts or rejects a candidate must call
    this. Four call sites used to keep their own partial copy of this logic and
    two of them were incomplete (the Apify discovery path never wrote
    acceptedCount, so a pipeline with candidates displayed "0 candidates"), which
    is exactly the drift this function exists to prevent.

    Counts are written per job with a positional `jobs.$` match rather than by
    array index, so a concurrent job add can't shift the write onto a sibling.
    """
    candidates_col = await get_collection("candidates")
    pipelines_col = await get_collection("candidatePipelines")
    oid = ObjectId(pipeline_id)

    pipeline = await pipelines_col.find_one({"_id": oid}, {"jobs.jobId": 1})
    if not pipeline:
        return {}
    now = datetime.utcnow()

    for job in pipeline.get("jobs") or []:
        job_id = job.get("jobId")
        if not job_id:
            continue
        scope = {"pipelineId": pipeline_id, "sourceJobIds": job_id}
        # `candidateCount` is counted directly rather than as accepted+rejected:
        # a candidate with no decision yet belongs to neither bucket.
        await pipelines_col.update_one(
            {"_id": oid, "jobs.jobId": job_id},
            {"$set": {
                "jobs.$.candidateCount": await candidates_col.count_documents(scope),
                "jobs.$.acceptedCount": await candidates_col.count_documents(
                    {**scope, "isAccepted": True}),
                "jobs.$.rejectedCount": await candidates_col.count_documents(
                    {**scope, "isAccepted": False}),
            }},
        )

    scope = {"pipelineId": pipeline_id}
    totals = {
        "totalCandidates": await candidates_col.count_documents(scope),
        "acceptedCount": await candidates_col.count_documents({**scope, "isAccepted": True}),
        "rejectedCount": await candidates_col.count_documents({**scope, "isAccepted": False}),
    }
    await pipelines_col.update_one({"_id": oid}, {"$set": {**totals, "updatedAt": now}})
    return totals


async def _finish(pipeline_id: str, job_id: str, *, status: str, **extras):
    pipelines_col = await get_collection("candidatePipelines")
    fields = {
        "jobs.$.searchStatus": status,
        "updatedAt": datetime.utcnow(),
    }
    for k, v in extras.items():
        fields[f"jobs.$.{k}"] = v
    await pipelines_col.update_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id},
        {"$set": fields},
    )


async def _search_candidates_for_job(
    pipeline_id: str, job_id: str, *, is_rerun: bool,
) -> None:
    """Background entry-point. Runs one Apollo people-search for one job and
    stores the candidates.

    All errors are swallowed and converted to ``searchStatus = failed`` so a
    crashed search never breaks the FastAPI event loop or other pipelines.
    """
    try:
        pipeline = await _claim_running(pipeline_id, job_id)
        if not pipeline:
            logger.info(
                "[Phase4] could not claim %s/%s — another worker has it or it was removed",
                pipeline_id, job_id,
            )
            return

        # Find the job entry inside the pipeline
        job_entry = next(
            (j for j in (pipeline.get("jobs") or []) if j.get("jobId") == job_id),
            None,
        )
        if not job_entry:
            return

        jobs_col = await get_collection("jobs")
        job_doc = await jobs_col.find_one({"_id": ObjectId(job_id)})
        if not job_doc:
            await _finish(pipeline_id, job_id, status="failed", searchError="job_not_found")
            return

        # ── resolve search params ──────────────────────────────────────────
        title = job_doc.get("title") or job_entry.get("jobTitle") or ""
        if not title:
            await _finish(pipeline_id, job_id, status="failed", searchError="no_title")
            return

        country = resolve_search_country(
            job_location=job_doc.get("location"),
            search_location=(job_doc.get("jobDetails") or {}).get("searchLocation"),
            company_location=pipeline.get("companyLocation"),
        )
        if not country:
            await _finish(
                pipeline_id, job_id,
                status="failed", searchError="no_location_available",
            )
            return

        matched_industry = pipeline.get("matchedIndustry") or None
        company_name = pipeline.get("companyName") or ""
        company_domain = pipeline.get("companyDomain") or ""

        # ── Apollo search (blocking I/O → thread) ──────────────────────────
        apollo = ApolloService()
        logger.info(
            "[Phase4] %s/%s search title=%r country=%r industry=%r",
            pipeline_id, job_id, title, country, matched_industry,
        )
        result = await asyncio.to_thread(
            apollo.search_candidates,
            title=title,
            location_country=country,
            current_industry=matched_industry,
            max_results=50,
        )
        people = result.get("people", [])
        applied_fallback = bool(result.get("applied_industry_fallback"))

        # ── post-filters: same-company drop + skip previously rejected ────
        candidates_col = await get_collection("candidates")
        # Previously-rejected apolloIds for this pipeline — skip on re-run only,
        # but applying this on first-run is safe and cheap.
        previously_rejected_ids = await candidates_col.distinct(
            "apolloId",
            {"pipelineId": pipeline_id, "isAccepted": False},
        )
        rejected_set = set(previously_rejected_ids)

        kept_people: List[dict] = []
        for p in people:
            if not p.get("id"):
                continue
            if _is_same_company(p, company_name, company_domain):
                continue
            if p["id"] in rejected_set:
                continue
            kept_people.append(p)

        # ── insert / append ───────────────────────────────────────────────
        now = datetime.utcnow()
        inserted = 0
        re_surfaced = 0
        for p in kept_people:
            score, reasons = _score_match(p, title, matched_industry)
            doc = _build_candidate_doc(
                p,
                pipeline_id=pipeline_id,
                job_id=job_id,
                applied_industry_fallback=applied_fallback,
                match_score=score,
                match_reasons=reasons,
                now=now,
            )
            # Try insert; if the (pipelineId, apolloId) compound key already
            # exists, append this job to sourceJobIds + a runHistory entry.
            try:
                await candidates_col.insert_one(doc)
                inserted += 1
            except Exception:
                # Duplicate (pipelineId, apolloId) — re-surfaced candidate.
                await candidates_col.update_one(
                    {"pipelineId": pipeline_id, "apolloId": p["id"]},
                    {
                        "$addToSet": {"sourceJobIds": job_id},
                        "$push": {
                            "runHistory": {
                                "runAt": now,
                                "jobId": job_id,
                                "isRerun": is_rerun,
                                "appliedIndustryFallback": applied_fallback,
                            }
                        },
                        "$set": {"updatedAt": now},
                    },
                )
                re_surfaced += 1

        # ── update pipeline + job counts ──────────────────────────────────
        await _finish(
            pipeline_id, job_id,
            status="completed",
            appliedIndustryFallback=applied_fallback,
            searchError=None,
        )
        counts = await recount_pipeline(pipeline_id)
        logger.info(
            "[Phase4] %s/%s done — inserted=%d re_surfaced=%d pipeline_total=%d "
            "(industry_fallback=%s)",
            pipeline_id, job_id, inserted, re_surfaced, counts.get("totalCandidates", 0),
            applied_fallback,
        )
    except Exception as exc:
        logger.error(
            "[Phase4] %s/%s crashed: %s", pipeline_id, job_id, exc, exc_info=True,
        )
        try:
            await _finish(pipeline_id, job_id, status="failed", searchError=str(exc)[:300])
        except Exception:
            pass


# ── Background bulk enrichment (Apollo → Apify) ──────────────────────────────
#
# Reuses the same per-(pipeline, job) background pattern as the candidate search,
# but tracks its own ``enrichStatus`` on the job entry so the UI can poll it
# independently of the search.


async def _set_enrich(pipeline_id: str, job_id: str, status: str, **extras) -> None:
    pipelines_col = await get_collection("candidatePipelines")
    fields: Dict[str, Any] = {
        "jobs.$.enrichStatus": status,
        "updatedAt": datetime.utcnow(),
    }
    for k, v in extras.items():
        fields[f"jobs.$.{k}"] = v
    await pipelines_col.update_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id},
        {"$set": fields},
    )


async def enqueue_job_enrich(
    pipeline_id: str, job_id: str, candidate_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Queue a background bulk-enrich for a job's (selected) candidates.

    ``candidate_ids`` narrows to specific candidates; None enriches every
    candidate in the job. Returns ``{"queued": True}``; raises ``ValueError``
    ("job_not_found") if the job isn't in the pipeline.
    """
    pipelines_col = await get_collection("candidatePipelines")
    now = datetime.utcnow()
    res = await pipelines_col.update_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id},
        {"$set": {"jobs.$.enrichStatus": "queued", "jobs.$.enrichError": None, "updatedAt": now}},
    )
    if res.matched_count == 0:
        raise ValueError("job_not_found")
    asyncio.create_task(_run_job_enrich(pipeline_id, job_id, candidate_ids))
    return {"queued": True}


async def _run_job_enrich(
    pipeline_id: str, job_id: str, candidate_ids: Optional[List[str]],
) -> None:
    """Background worker: enrich the selected candidates through Apollo → Apify."""
    from app.services.candidate_enrichment import bulk_enrich
    from app.services import cost_service
    try:
        await _set_enrich(pipeline_id, job_id, "running", enrichError=None)
        async with cost_service.cost_context(
            cost_service.STAGE_CANDIDATE, pipelineId=pipeline_id, jobId=job_id,
        ):
            if candidate_ids:
                summary = await bulk_enrich(candidate_ids=candidate_ids)
            else:
                summary = await bulk_enrich(pipeline_id=pipeline_id, job_id=job_id)
        await _set_enrich(
            pipeline_id, job_id, "completed", enrichCounts=summary, enrichError=None,
        )
        logger.info("[Phase4] enrich %s/%s done: %s", pipeline_id, job_id, summary)
    except Exception as exc:  # noqa: BLE001
        logger.error("[Phase4] enrich %s/%s crashed: %s", pipeline_id, job_id, exc, exc_info=True)
        try:
            await _set_enrich(pipeline_id, job_id, "failed", enrichError=str(exc)[:300])
        except Exception:
            pass


# ── Apify discovery: search questionnaire → candidates → auto-enrich ────────


def _apify_score(profile: Dict[str, Any], search_query: str) -> Tuple[int, List[str]]:
    """Cheap title-overlap score for a search profile (no extra API calls)."""
    title = (profile.get("currentTitle") or "").lower()
    target = (search_query or "").lower()
    target_tokens = {t for t in re.split(r"\W+", target) if len(t) > 2}
    title_tokens = {t for t in re.split(r"\W+", title) if len(t) > 2}
    if not target_tokens:
        return 50, ["apify_search"]
    ratio = len(target_tokens & title_tokens) / len(target_tokens)
    if ratio >= 0.8:
        return 90, ["title_exact_match"]
    if ratio >= 0.5:
        return 70, ["title_partial_match"]
    if ratio > 0:
        return 45, ["title_token_overlap"]
    return 30, ["apify_search"]


def _build_apify_candidate_doc(
    profile: Dict[str, Any], *, pipeline_id: str, search_query: str, now: datetime,
) -> Dict[str, Any]:
    """$setOnInsert fields for a candidate sourced from the Apify search actor.
    ``apolloId`` holds the LinkedIn profile id so the (pipelineId, apolloId)
    unique index still dedups; ``source`` marks it Apify-sourced."""
    score, reasons = _apify_score(profile, search_query)
    return {
        "pipelineId": pipeline_id,
        "apolloId": profile["profileId"],
        "source": "apify_search",
        "externalLinkedinUrl": profile.get("linkedinUrl") or "",
        "firstName": profile.get("firstName") or "Unknown",
        "lastName": profile.get("lastName") or "",
        "displayName": profile.get("displayName") or f"{profile.get('firstName','')} {profile.get('lastName','')}".strip(),
        "headline": "",
        "currentTitle": profile.get("currentTitle") or "",
        "currentCompany": profile.get("currentCompany") or "",
        "currentCompanyDomain": "",
        "location": profile.get("location") or "",
        "photoUrl": profile.get("photoUrl") or "",
        "matchScore": score,
        "matchReasons": reasons,
        "isAccepted": True,
        "rejectionReason": None,
        "decidedAt": None,
        "isEnriched": False,
        "enrichedAt": None,
        "enrichedData": None,
        "isApifyEnriched": False,
        "runHistory": [{"runAt": now, "jobId": None, "isRerun": False, "appliedIndustryFallback": False}],
        "createdAt": now,
    }


async def _claim_discover(pipeline_id: str, job_id: str) -> bool:
    """Atomic → searchStatus 'running' from any non-running state. False if a
    discovery is already in flight for this job."""
    pipelines_col = await get_collection("candidatePipelines")
    res = await pipelines_col.update_one(
        {"_id": ObjectId(pipeline_id),
         "jobs": {"$elemMatch": {"jobId": job_id, "searchStatus": {"$ne": "running"}}}},
        {"$set": {"jobs.$.searchStatus": "running", "jobs.$.searchError": None,
                  "jobs.$.lastSearchedAt": datetime.utcnow(), "updatedAt": datetime.utcnow()}},
    )
    return res.modified_count > 0


async def enqueue_job_discover(
    pipeline_id: str, job_id: str, filters: Dict[str, Any], max_items: int = 25,
    *,
    auto_broaden: bool = False,
    hints: Optional[Dict[str, Any]] = None,
    ladder: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Kick off Apify search discovery for a job (background). Poll the job's
    ``searchStatus`` then ``enrichStatus``.

    ``auto_broaden`` turns on the agentic recovery loop: a search that returns
    zero candidates is retried with agent-relaxed filters (see
    ``_discover_candidates_for_job``). ``hints`` is the recruiter's optional
    brief, and ``ladder`` the Strategist's pre-planned fallbacks — both are
    context for the Broadener and are safe to omit.
    """
    pipelines_col = await get_collection("candidatePipelines")
    job_exists = await pipelines_col.find_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id}, {"_id": 1})
    if not job_exists:
        raise ValueError("job_not_found")
    asyncio.create_task(_discover_candidates_for_job(
        pipeline_id, job_id, filters, max_items,
        auto_broaden=auto_broaden, hints=hints, ladder=ladder,
    ))
    return {"queued": True}


async def _run_search(
    pipeline_id: str, job_id: str, filters: Dict[str, Any], max_items: int,
) -> List[Dict[str, Any]]:
    """One PAID Apify search → parsed short profiles. Metered by the caller's stage."""
    from app.services.apify_search_service import get_apify_search_service, parse_short_profile
    from app.services import cost_service

    async with cost_service.cost_context(
        cost_service.STAGE_CANDIDATE, pipelineId=pipeline_id, jobId=job_id,
    ):
        service = get_apify_search_service()
        items = await asyncio.to_thread(service.search, filters, max_items=max_items)
    return [p for p in (parse_short_profile(i) for i in items) if p and p.get("profileId")]


async def _store_profiles(
    profiles: List[Dict[str, Any]], *, pipeline_id: str, job_id: str,
    search_query: str, now: datetime,
) -> List[str]:
    """Upsert short profiles as candidates. Returns their ids (new and existing)."""
    candidates_col = await get_collection("candidates")
    cand_ids: List[str] = []
    for p in profiles:
        doc = _build_apify_candidate_doc(
            p, pipeline_id=pipeline_id, search_query=search_query, now=now)
        try:
            res = await candidates_col.update_one(
                {"pipelineId": pipeline_id, "apolloId": doc["apolloId"]},
                {"$setOnInsert": doc,
                 "$addToSet": {"sourceJobIds": job_id},
                 "$set": {"updatedAt": now}},
                upsert=True,
            )
            if res.upserted_id:
                cand_ids.append(str(res.upserted_id))
            else:
                ex = await candidates_col.find_one(
                    {"pipelineId": pipeline_id, "apolloId": doc["apolloId"]}, {"_id": 1})
                if ex:
                    cand_ids.append(str(ex["_id"]))
        except DuplicateKeyError:
            continue
    return cand_ids


async def _record_attempts(
    pipeline_id: str, job_id: str, attempts: List[Any],
) -> None:
    """Persist the attempt timeline on the job so the UI can show the agent's work."""
    pipelines_col = await get_collection("candidatePipelines")
    await pipelines_col.update_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id},
        {"$set": {
            "jobs.$.searchAttempts": [a.model_dump(mode="json") for a in attempts],
            "updatedAt": datetime.utcnow(),
        }},
    )


async def _search_with_broadening(
    pipeline_id: str, job_id: str, filters: Dict[str, Any], max_items: int,
    *, auto_broaden: bool, hints: Optional[Dict[str, Any]],
    ladder: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Any], Dict[str, Any]]:
    """Search, and if it returns zero, let the Broadener retry with wider filters.

    Returns (profiles, attempts, winning_filters). Stops at the FIRST attempt that
    returns anything — we're recovering from zero, not maximising recall.

    Cost is bounded three ways: ``SOURCING_MAX_BROADEN_ATTEMPTS`` caps the retries,
    the Broadener refuses to repeat a filter set it already tried, and it stops
    early once the filters are broad enough that zero means "not on LinkedIn".
    """
    from app.config import settings
    from app.services.sourcing import SearchAttempt, build_brief, next_attempt
    from app.services.sourcing.models import BroadeningStep

    attempts: List[SearchAttempt] = []
    current = dict(filters)
    action, reasoning = "initial", ""
    max_retries = max(0, int(settings.SOURCING_MAX_BROADEN_ATTEMPTS))
    brief = None
    planned: List[BroadeningStep] = []
    if ladder:
        # A malformed ladder from the client must not break the search — the
        # Broadener works reactively without it.
        try:
            planned = [BroadeningStep(**s) for s in ladder]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Discover] ignoring malformed broadening ladder: %s", exc)

    while True:
        try:
            profiles = await _run_search(pipeline_id, job_id, current, max_items)
            error = None
        except Exception as exc:  # noqa: BLE001 — a dead attempt shouldn't kill the loop
            logger.warning("[Discover] %s/%s attempt %d failed: %s",
                           pipeline_id, job_id, len(attempts) + 1, exc)
            profiles, error = [], str(exc)[:200]

        attempts.append(SearchAttempt(
            attempt=len(attempts) + 1, action=action, reasoning=reasoning,
            filters=current, resultCount=len(profiles), error=error,
        ))
        await _record_attempts(pipeline_id, job_id, attempts)

        if profiles:
            return profiles, attempts, current
        if not auto_broaden or len(attempts) > max_retries:
            return [], attempts, current

        logger.info("[Discover] %s/%s attempt %d returned 0 — broadening",
                    pipeline_id, job_id, len(attempts))
        if brief is None:
            brief = await build_brief(pipeline_id, job_id, hints)
        decision = await next_attempt(brief, attempts, planned)
        if decision is None:
            logger.info("[Discover] %s/%s broadening stopped after %d attempt(s)",
                        pipeline_id, job_id, len(attempts))
            return [], attempts, current
        current = decision.filters.to_search_input()
        action, reasoning = decision.action, decision.reasoning


async def _discover_candidates_for_job(
    pipeline_id: str, job_id: str, filters: Dict[str, Any], max_items: int,
    *,
    auto_broaden: bool = False,
    hints: Optional[Dict[str, Any]] = None,
    ladder: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Background: search LinkedIn via Apify, store the results as candidates,
    then auto-enrich every one via the profile scraper.

    With ``auto_broaden`` the search is agentic: a zero-result attempt is retried
    with filters the Broadener relaxes based on what already failed, instead of
    handing the recruiter an empty list.
    """
    if not await _claim_discover(pipeline_id, job_id):
        logger.info("[Discover] %s/%s already running — skip", pipeline_id, job_id)
        return

    try:
        profiles, attempts, used_filters = await _search_with_broadening(
            pipeline_id, job_id, filters, max_items,
            auto_broaden=auto_broaden, hints=hints, ladder=ladder,
        )
        search_query = (used_filters.get("searchQuery") or "").strip()

        now = datetime.utcnow()
        cand_ids = await _store_profiles(
            profiles, pipeline_id=pipeline_id, job_id=job_id,
            search_query=search_query, now=now,
        )

        await _finish(pipeline_id, job_id, status="completed",
                      lastSearchedAt=now, searchError=None)
        # Counts (per-job AND the pipeline rollup the list UI reads) come from
        # the shared recount — this path used to set candidateCount only, which
        # left every Apify-sourced pipeline showing "0 candidates".
        await recount_pipeline(pipeline_id)
        logger.info("[Discover] %s/%s stored %d candidate(s) after %d attempt(s)",
                    pipeline_id, job_id, len(cand_ids), len(attempts))

        # Auto-enrich every discovered candidate (Apify profile scraper only).
        if cand_ids:
            await _set_enrich(pipeline_id, job_id, "running", enrichError=None)
            from app.services.candidate_enrichment import enrich_candidates
            from app.services import cost_service
            try:
                async with cost_service.cost_context(
                    cost_service.STAGE_CANDIDATE, pipelineId=pipeline_id, jobId=job_id,
                ):
                    summary = await enrich_candidates(candidate_ids=cand_ids)
                await _set_enrich(pipeline_id, job_id, "completed",
                                  enrichCounts=summary, enrichError=None)
                logger.info("[Discover] %s/%s enriched: %s", pipeline_id, job_id, summary)
            except Exception as e:  # noqa: BLE001 — enrichment failure mustn't fail discovery
                await _set_enrich(pipeline_id, job_id, "failed", enrichError=str(e)[:300])
    except Exception as exc:  # noqa: BLE001
        logger.error("[Discover] %s/%s failed: %s", pipeline_id, job_id, exc, exc_info=True)
        try:
            await _finish(pipeline_id, job_id, status="failed", searchError=str(exc)[:300])
        except Exception:
            pass
