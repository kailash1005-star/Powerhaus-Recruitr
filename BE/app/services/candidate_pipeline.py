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
        # Provenance of matchScore. "sourcing_heuristic" = the cheap title-overlap
        # number from search time; a real match run overwrites it and stamps
        # "match_run". The UI renders heuristic scores as provisional.
        "matchScoreSource": "sourcing_heuristic",
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

    ENGINE ROUTING — this used to be the single worst accuracy bug in sourcing:
    "discover" ran the Apify/LinkedIn engine while this rerun silently ran the
    legacy Apollo engine (country-only location, include_similar_titles, title
    shrinking), so one click replaced LinkedIn-grade results with country-wide
    noise. Rerun now re-executes the SAME agentic discovery the job last ran
    (stored filters), or derives fresh filters via the Strategist for jobs that
    never had any. Apollo remains only as the explicit fallback when Apify is
    not configured at all, and the engine used is stamped on the job.
    """
    from app.config import settings

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

    if settings.APIFY_TOKEN:
        doc = await pipelines_col.find_one(
            {"_id": pipeline_oid, "jobs.jobId": job_id}, {"jobs.$": 1})
        entry = (doc or {}).get("jobs", [{}])[0]
        filters = entry.get("lastDiscoverFilters")
        max_items = int(entry.get("lastDiscoverMaxItems") or 25)
        hints = entry.get("lastDiscoverHints")
        ladder = entry.get("lastDiscoverLadder")
        anchor = entry.get("lastDiscoverAnchor")
        adjacent = entry.get("adjacentTitles")

        if not filters:
            # Legacy job that never went through discovery — let the Strategist
            # derive filters from the JD (one LLM call, no vendor spend). Falls
            # through to Apollo only if even that fails.
            try:
                from app.services.sourcing import build_brief, propose_strategy
                brief = await build_brief(pipeline_id, job_id, None)
                strategy = await propose_strategy(brief)
                if not strategy.filters.is_empty():
                    filters = strategy.filters.to_search_input()
                    ladder = [s.model_dump(mode="json") for s in strategy.broadeningLadder]
                    anchor = strategy.domainAnchor.model_dump(mode="json")
                    adjacent = list(strategy.adjacentTitles)
            except Exception as exc:  # noqa: BLE001 — Strategist prefill is best-effort
                logger.warning("[Rerun] %s/%s Strategist prefill failed: %s",
                               pipeline_id, job_id, exc)

        if filters:
            asyncio.create_task(_discover_candidates_for_job(
                pipeline_id, job_id, filters, max_items,
                auto_broaden=True, hints=hints, ladder=ladder,
                anchor=anchor, adjacent_titles=adjacent,
            ))
            return {"queued": True, "engine": "apify_discovery"}
        logger.warning("[Rerun] %s/%s no usable discovery filters — falling back to Apollo",
                       pipeline_id, job_id)

    asyncio.create_task(_search_candidates_for_job(pipeline_id, job_id, is_rerun=True))
    return {"queued": True, "engine": "apollo_legacy"}


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
                "jobs.$.searchEngine": "apollo_legacy",
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
            # DuplicateKeyError ONLY — the old bare `except Exception` classified
            # every write failure (validation, connection loss) as "duplicate"
            # and silently mislabelled it as a re-surfaced candidate.
            try:
                await candidates_col.insert_one(doc)
                inserted += 1
            except DuplicateKeyError:
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


async def _enrich_for_job(
    pipeline_id: str, job_id: str, candidate_ids: Optional[List[str]],
) -> Dict[str, Any]:
    """Enrich a job's candidates, routing by SOURCE.

    Apify-discovered candidates carry a LinkedIn URN in ``apolloId`` (not an
    Apollo person id), so the Apollo /people/match stage in ``bulk_enrich`` can
    only ever fail for them — one wasted, paid Apollo call each — before falling
    through to the Apify scrape that actually works. When every target is
    Apify-sourced we skip straight to the Apify-only path (exactly what the old
    auto-enrich did). Candidates that came from Apollo still get both stages
    (verified email + deep profile). The returned summary always carries the
    ``apollo_enriched``/``apify_enriched``/``not_found`` keys the UI reads.
    """
    from app.services.candidate_enrichment import bulk_enrich, enrich_candidates

    candidates_col = await get_collection("candidates")
    scope: Dict[str, Any] = {"pipelineId": pipeline_id}
    if candidate_ids:
        scope["_id"] = {"$in": [ObjectId(c) for c in candidate_ids]}
    else:
        scope["sourceJobIds"] = job_id

    needs_apollo = await candidates_col.count_documents(
        {**scope, "source": {"$ne": "apify_search"}}) > 0

    if needs_apollo:
        if candidate_ids:
            return await bulk_enrich(candidate_ids=candidate_ids)
        return await bulk_enrich(pipeline_id=pipeline_id, job_id=job_id)

    # Pure Apify discovery set → Apify-only, normalized to the UI's key shape.
    if candidate_ids:
        s = await enrich_candidates(candidate_ids=candidate_ids)
    else:
        s = await enrich_candidates(pipeline_id=pipeline_id, job_id=job_id)
    return {
        "selected": s.get("selected", 0),
        "apollo_enriched": 0, "apollo_failed": 0,
        "apify_enriched": s.get("enriched", 0),
        "cached": s.get("cached", 0),
        "not_found": s.get("not_found", 0),
        "skipped": s.get("skipped", 0),
    }


async def _run_job_enrich(
    pipeline_id: str, job_id: str, candidate_ids: Optional[List[str]],
) -> None:
    """Background worker: enrich the selected candidates (or all in the job)."""
    from app.services import cost_service
    try:
        await _set_enrich(pipeline_id, job_id, "running", enrichError=None)
        async with cost_service.cost_context(
            cost_service.STAGE_CANDIDATE, pipelineId=pipeline_id, jobId=job_id,
        ):
            summary = await _enrich_for_job(pipeline_id, job_id, candidate_ids)
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
        # Which search channel(s) found this person — "title" (filtered title
        # search), "keyword" (fuzzy profile-keyword search), or both. Shown in
        # the UI so the recruiter can see WHY each candidate is in the list.
        "sourceChannels": list(profile.get("channels") or ["title"]),
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
        # See _build_candidate_doc — provisional until a match run rescores it.
        "matchScoreSource": "sourcing_heuristic",
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
                  "jobs.$.searchEngine": "apify_discovery",
                  "jobs.$.lastSearchedAt": datetime.utcnow(), "updatedAt": datetime.utcnow()}},
    )
    return res.modified_count > 0


async def enqueue_job_discover(
    pipeline_id: str, job_id: str, filters: Dict[str, Any], max_items: int = 25,
    *,
    auto_broaden: bool = False,
    hints: Optional[Dict[str, Any]] = None,
    ladder: Optional[List[Dict[str, Any]]] = None,
    anchor: Optional[Dict[str, Any]] = None,
    adjacent_titles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Kick off Apify search discovery for a job (background). Poll the job's
    ``searchStatus`` then ``enrichStatus``.

    ``auto_broaden`` turns on the agentic recovery loop: a search that returns
    zero candidates is retried with agent-relaxed filters (see
    ``_discover_candidates_for_job``). ``hints`` is the recruiter's optional
    brief, ``ladder`` the Strategist's pre-planned fallbacks, ``anchor`` its
    two-tier domain anchor and ``adjacent_titles`` the opt-in neighbouring
    specialties — all context for the recovery/widen flows and safe to omit.
    """
    pipelines_col = await get_collection("candidatePipelines")
    # Persist what this discovery ran with, so "rerun" can replay the SAME
    # search instead of falling back to a different engine (that fallback is
    # exactly how vague Apollo results used to replace LinkedIn results).
    res = await pipelines_col.update_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id},
        {"$set": {
            "jobs.$.lastDiscoverFilters": filters,
            "jobs.$.lastDiscoverMaxItems": max_items,
            "jobs.$.lastDiscoverHints": hints,
            "jobs.$.lastDiscoverLadder": ladder,
            "jobs.$.lastDiscoverAnchor": anchor,
            "jobs.$.adjacentTitles": adjacent_titles or [],
            "updatedAt": datetime.utcnow(),
        }},
    )
    if res.matched_count == 0:
        raise ValueError("job_not_found")
    asyncio.create_task(_discover_candidates_for_job(
        pipeline_id, job_id, filters, max_items,
        auto_broaden=auto_broaden, hints=hints, ladder=ladder,
        anchor=anchor, adjacent_titles=adjacent_titles,
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


def _keyword_channel_filters(filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The keyword-only variant of a filter set, or None when it adds nothing.

    LinkedIn headlines are self-description: the SAP HCM consultant whose
    headline says "IT-Consultant bei X" is invisible to a title-filtered search
    but IS found by the fuzzy keyword channel, because the actor's searchQuery
    matches profile keywords, not just the title line. This is one of the
    signals LinkedIn's own search uses and a title-only search silently loses.

    The variant drops the title filters and keeps everything else (locations,
    languages, enum filters, exclusions) so the recruiter's explicit constraints
    still hold. Returns None when the base search carries no titles (it already
    IS a keyword search) or no query (nothing to search by).
    """
    if not (filters.get("currentJobTitles") or filters.get("pastJobTitles")):
        return None
    if not (filters.get("searchQuery") or "").strip():
        return None
    return {k: v for k, v in filters.items()
            if k not in ("currentJobTitles", "pastJobTitles")}


async def _run_search_channels(
    pipeline_id: str, job_id: str, filters: Dict[str, Any], max_items: int,
    *, include_keyword_channel: bool,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Run the title-channel search plus (optionally) the keyword channel, merged.

    Returns (profiles, channel_counts). Each profile carries ``channels`` —
    which searches found it — so ranking can prefer corroborated hits and the
    UI can say WHY a candidate is in the list.

    Failure model: the title channel is authoritative — its errors propagate
    (the broadening loop owns retry/abort semantics, including the quota-abort).
    The keyword channel is a recall add-on: its failure is logged and skipped,
    never fatal to a search that already has results in hand.
    """
    primary = "title" if filters.get("currentJobTitles") else "keyword"
    profiles = await _run_search(pipeline_id, job_id, filters, max_items)
    for p in profiles:
        p["channels"] = [primary]
    counts = {primary: len(profiles)}

    kw_filters = _keyword_channel_filters(filters) if include_keyword_channel else None
    if kw_filters:
        try:
            kw_profiles = await _run_search(pipeline_id, job_id, kw_filters, max_items)
        except Exception as exc:  # noqa: BLE001 — recall add-on, never fatal
            logger.warning("[Discover] %s/%s keyword channel failed (title channel kept): %s",
                           pipeline_id, job_id, exc)
            kw_profiles = []
        counts["keyword"] = len(kw_profiles)
        by_id = {p["profileId"]: p for p in profiles}
        for p in kw_profiles:
            existing = by_id.get(p["profileId"])
            if existing is not None:
                # Found by BOTH channels — corroboration, the strongest signal.
                if "keyword" not in existing["channels"]:
                    existing["channels"].append("keyword")
            else:
                p["channels"] = ["keyword"]
                profiles.append(p)
                by_id[p["profileId"]] = p
    return profiles, counts


def _channel_screen_policy(
    keep: bool, verdict: Dict[str, Any], channels: List[str],
) -> Tuple[bool, Dict[str, Any]]:
    """Channel-aware adjustments to the title-only prescreen verdict.

    A keyword-channel hit whose TITLE shares no vocabulary with the role is not
    a random stranger: the actor matched the query against the profile's own
    content ("IT-Consultant bei X" whose profile says SAP HCM). The title-only
    gate can't see that evidence, so it must not be allowed to drop on it —
    false DROPs are unrecoverable, a false KEEP costs one $0.004 scrape and the
    matcher catches it a minute later.

    Corroboration: a hit found independently by BOTH the title search and the
    keyword search is the strongest pre-enrichment signal there is — it gets a
    small rank bonus (capped) so it sorts above single-channel hits.
    """
    if not keep and "keyword" in channels:
        keep = True
        verdict = {
            **verdict,
            "decision": "keep",
            "score": max(float(verdict.get("score") or 0.0), 30.0),
            "reasons": [
                "Profile content matches the search keywords — kept for "
                "enrichment even though the title alone doesn't show it.",
                *(verdict.get("reasons") or []),
            ],
        }
    if keep and len(channels) > 1 and verdict.get("score") is not None:
        verdict = {**verdict, "score": min(95.0, float(verdict["score"]) + 5.0)}
    return keep, verdict


async def _store_profiles(
    profiles: List[Dict[str, Any]], *, pipeline_id: str, job_id: str,
    search_query: str, now: datetime,
    requirements: Optional[Dict[str, Any]] = None,
    target_titles: Optional[List[str]] = None,
    requested_location: Optional[str] = None,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Upsert short profiles as candidates, pre-screening each against the role.

    Returns (ids_worth_enriching, screening_verdicts). Screened-out profiles are
    still STORED — marked `isAccepted: False` with their verdict — never dropped
    on the floor. The recruiter can see who was skipped and why, and re-include
    them; only the Apify enrichment spend is withheld.

    Two gates, cheapest-first:
      1. Location (deterministic) — a candidate in the WRONG COUNTRY is rejected
         outright (the Bavaria→India leak). Exact, free, and it cannot be wrong
         about geography the way an LLM can. Wrong region / right country is kept
         and flagged (remote and relocation are legitimate).
      2. Title pre-screen (the existing role-relevance heuristic).
    """
    from app.config import settings
    from app.services import location_resolver, prescreen_service

    candidates_col = await get_collection("candidates")
    cand_ids: List[str] = []
    verdicts: List[Dict[str, Any]] = []
    gate_on = (settings.SOURCING_LOCATION_GATE or "off").lower() == "country"

    for p in profiles:
        doc = _build_apify_candidate_doc(
            p, pipeline_id=pipeline_id, search_query=search_query, now=now)
        channels = list(p.get("channels") or ["title"])

        # ── Gate 1: location (deterministic, runs before the title screen) ──
        loc_verdict = None
        if gate_on and requested_location:
            loc_verdict = location_resolver.location_verdict(
                requested_location, p.get("location") or doc.get("location"))

        if loc_verdict and loc_verdict["decision"] == "country_mismatch":
            verdict = {
                "decision": "drop", "score": 0.0, "roleFit": 0.0, "matchedVia": None,
                "reasons": [f"Location gate: {loc_verdict['reason']}"],
                "location": loc_verdict, "at": now, "channels": channels,
            }
            verdicts.append({**verdict, "title": p.get("currentTitle"),
                             "name": doc.get("displayName")})
            doc["prescreen"] = verdict
            doc["isAccepted"] = False
            doc["rejectionReason"] = f"Location mismatch — {loc_verdict['reason']}"
            doc["locationMismatch"] = True
            doc["decidedAt"] = now
            doc["matchScore"] = 0
            doc["matchReasons"] = verdict["reasons"]
            try:
                await candidates_col.update_one(
                    {"pipelineId": pipeline_id, "apolloId": doc["apolloId"]},
                    {"$setOnInsert": doc, "$addToSet": {"sourceJobIds": job_id},
                     "$set": {"updatedAt": now}}, upsert=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Discover] location-gated upsert failed: %s", exc)
            continue

        # ── Gate 2: title pre-screen ──
        if settings.PRESCREEN_ENABLED:
            keep, verdict = prescreen_service.screen(
                p, requirements=requirements, target_titles=target_titles,
                min_score=settings.PRESCREEN_MIN_SCORE,
            )
            keep, verdict = _channel_screen_policy(keep, verdict, channels)
        else:
            keep, verdict = True, {"decision": "keep", "score": None,
                                   "reasons": ["Pre-screen disabled."]}
        verdict = {**verdict, "at": now, "channels": channels}
        if loc_verdict:
            verdict["location"] = loc_verdict
            if loc_verdict["decision"] == "region_mismatch":
                doc["locationFlag"] = loc_verdict["reason"]
        verdicts.append({**verdict, "title": p.get("currentTitle"),
                         "name": doc.get("displayName")})

        doc["prescreen"] = verdict
        # The prescreen score IS the sourcing heuristic: it grades the same
        # free signal the old fixed 90/70/45/30 title-overlap score did, but
        # against the ROLE (target titles + must-haves), continuously, and
        # channel-aware — so the table's default matchScore sort is a real
        # relevance ranking, not four buckets. Provisional either way
        # (matchScoreSource: sourcing_heuristic) until a match run rescores.
        if verdict.get("score") is not None:
            doc["matchScore"] = int(round(float(verdict["score"])))
            doc["matchReasons"] = list(verdict.get("reasons") or [])[:3]
        if not keep:
            doc["isAccepted"] = False
            doc["rejectionReason"] = verdict["reasons"][0] if verdict.get("reasons") else "Pre-screened out"
            doc["decidedAt"] = now

        try:
            res = await candidates_col.update_one(
                {"pipelineId": pipeline_id, "apolloId": doc["apolloId"]},
                {"$setOnInsert": doc,
                 "$addToSet": {"sourceJobIds": job_id},
                 "$set": {"updatedAt": now}},
                upsert=True,
            )
            if res.upserted_id:
                cid = str(res.upserted_id)
            else:
                ex = await candidates_col.find_one(
                    {"pipelineId": pipeline_id, "apolloId": doc["apolloId"]}, {"_id": 1})
                cid = str(ex["_id"]) if ex else None
            # Only survivors go on to be enriched — this is where the money stops.
            if cid and keep:
                cand_ids.append(cid)
        except DuplicateKeyError:
            continue

    return cand_ids, verdicts


def _settings_qa_enabled() -> bool:
    from app.config import settings
    return bool(settings.SOURCING_QA_ENABLED)


async def _audit_sourcing_results(
    pipeline_id: str, job_id: str, filters: Dict[str, Any],
    requirements: Dict[str, Any], cand_ids: List[str], location_rejected: int,
) -> None:
    """Run the sourcing auditor over the kept candidates and record the report.

    Builds the auditor's view from the stored candidate rows (title/company/
    channels) so it audits exactly what the recruiter will see."""
    from app.services import sourcing_qa_service

    candidates_col = await get_collection("candidates")
    kept: List[Dict[str, Any]] = []
    for cid in cand_ids:
        try:
            d = await candidates_col.find_one(
                {"_id": ObjectId(cid)},
                {"currentTitle": 1, "currentCompany": 1, "location": 1, "sourceChannels": 1})
        except Exception:  # noqa: BLE001
            d = None
        if d:
            kept.append({
                "candidateId": cid,
                "title": d.get("currentTitle"),
                "company": d.get("currentCompany"),
                "location": d.get("location"),
                "channels": d.get("sourceChannels") or [],
            })
    if not kept:
        return

    query = {
        "title": (requirements.get("title")
                  or (filters.get("currentJobTitles") or [None])[0]),
        "targetTitles": filters.get("currentJobTitles") or [],
        "mustHaveSkills": requirements.get("mustHaveSkills") or [],
        "seniority": requirements.get("seniority"),
    }
    from app.database import get_database
    summary = await sourcing_qa_service.audit_results(
        await get_database(),
        pipeline_id=pipeline_id, job_id=job_id,
        jd_title=query["title"] or "", query=query,
        kept=kept, location_rejected=location_rejected,
    )
    if summary.get("mismatchesFlagged"):
        logger.info("[Discover] %s/%s sourcing QA flagged %d off-specialty result(s)",
                    pipeline_id, job_id, summary["mismatchesFlagged"])


async def _record_prescreen(
    pipeline_id: str, job_id: str, verdicts: List[Dict[str, Any]],
) -> None:
    """Persist what the gate did, so a thin pipeline is explainable rather than
    mysterious. Best-effort — telemetry must never fail a discovery run."""
    kept = [v for v in verdicts if v.get("decision") != "drop"]
    dropped = [v for v in verdicts if v.get("decision") == "drop"]
    try:
        pipelines_col = await get_collection("candidatePipelines")
        await pipelines_col.update_one(
            {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id},
            {"$set": {
                "jobs.$.prescreen": {
                    "total": len(verdicts),
                    "kept": len(kept),
                    "dropped": len(dropped),
                    # Enough to justify the gate without storing every profile twice.
                    "droppedSamples": [
                        {"name": v.get("name"), "title": v.get("title"),
                         "score": v.get("score"), "reason": (v.get("reasons") or [None])[0]}
                        for v in dropped[:20]
                    ],
                    "at": datetime.utcnow(),
                },
                "updatedAt": datetime.utcnow(),
            }},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Discover] could not record pre-screen for %s/%s: %s",
                       pipeline_id, job_id, exc)


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
    anchor: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Any], Dict[str, Any]]:
    """Search, and if it returns zero, let the Broadener retry with wider filters.

    Returns (profiles, attempts, winning_filters). Stops at the FIRST attempt that
    returns anything — we're recovering from zero, not maximising recall. The
    initial attempt runs BOTH channels (title + keyword) merged; retries rerun
    only the title channel, because the keyword channel doesn't carry the
    filters being relaxed.

    Cost is bounded three ways: ``SOURCING_MAX_BROADEN_ATTEMPTS`` caps the retries,
    the Broadener refuses to repeat a filter set it already tried, and it stops
    early once the filters are broad enough that zero means "not on LinkedIn".
    The Broadener may relax enums/companies/location/language ONLY — the titles
    and query are clamped in code to the recruiter-approved target
    (``broadener.lock_target``), so drifting into a neighbouring profession is
    structurally impossible.
    """
    from app.config import settings
    from app.services.apify_profile_service import ApifyRunFailed
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
        channel_counts: Optional[Dict[str, int]] = None
        try:
            profiles, channel_counts = await _run_search_channels(
                pipeline_id, job_id, current, max_items,
                include_keyword_channel=not attempts,  # initial attempt only
            )
            error = None
        except ApifyRunFailed as exc:
            # An account/billing refusal is not a filter problem: every retry hits
            # the same wall and returns the same nothing. Broadening through it
            # burns the whole budget and ends by telling the recruiter their role
            # has no candidates, when in fact no search ever ran.
            logger.error("[Discover] %s/%s aborting — Apify refused the run: %s",
                         pipeline_id, job_id, exc)
            attempts.append(SearchAttempt(
                attempt=len(attempts) + 1, action=action, reasoning=reasoning,
                filters=current, resultCount=0, error=str(exc)[:300],
            ))
            await _record_attempts(pipeline_id, job_id, attempts)
            raise
        except Exception as exc:  # noqa: BLE001 — a dead attempt shouldn't kill the loop
            logger.warning("[Discover] %s/%s attempt %d failed: %s",
                           pipeline_id, job_id, len(attempts) + 1, exc)
            profiles, error = [], str(exc)[:200]

        attempts.append(SearchAttempt(
            attempt=len(attempts) + 1, action=action, reasoning=reasoning,
            filters=current, resultCount=len(profiles),
            channelCounts=channel_counts, error=error,
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
        decision = await next_attempt(brief, attempts, planned, strategy_anchor=anchor)
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
    anchor: Optional[Dict[str, Any]] = None,
    adjacent_titles: Optional[List[str]] = None,
) -> None:
    """Background: search LinkedIn via Apify (title + keyword channels), store
    the results as candidates ranked by role relevance, and stop for the
    recruiter's enrichment decision.

    With ``auto_broaden`` the search is agentic: a zero-result attempt is retried
    with filters the Broadener relaxes based on what already failed, instead of
    handing the recruiter an empty list. When the search still comes up short of
    ``SOURCING_TARGET_CANDIDATES``, the job carries a ``searchShortfall`` payload
    offering the Strategist's adjacent-specialty titles as recruiter-opt-in
    chips — the tool never widens the specialty on its own.
    """
    if not await _claim_discover(pipeline_id, job_id):
        logger.info("[Discover] %s/%s already running — skip", pipeline_id, job_id)
        return

    try:
        profiles, attempts, used_filters = await _search_with_broadening(
            pipeline_id, job_id, filters, max_items,
            auto_broaden=auto_broaden, hints=hints, ladder=ladder, anchor=anchor,
        )
        search_query = (used_filters.get("searchQuery") or "").strip()

        # The role spec is what the matcher will grade these people against, so
        # screen them against the same thing rather than against the fuzzy query.
        requirements: Dict[str, Any] = {}
        try:
            from app.database import get_database
            from app.services import role_spec_service

            spec = await role_spec_service.get_or_create_for_job(await get_database(), job_id)
            requirements = (spec or {}).get("requirements") or {}
        except Exception as exc:  # noqa: BLE001 — no spec ⇒ screen() keeps everything
            logger.warning("[Discover] %s/%s no role spec for pre-screen: %s",
                           pipeline_id, job_id, exc)

        # The location the recruiter ASKED for — the original filters, not the
        # broadened ones (the Broadener may relax location as a last resort; the
        # gate must judge against the recruiter's actual instruction).
        from app.services import location_resolver as _locres
        req_location = _locres.requested_location(filters, requirements)

        now = datetime.utcnow()
        cand_ids, verdicts = await _store_profiles(
            profiles, pipeline_id=pipeline_id, job_id=job_id,
            search_query=search_query, now=now,
            requirements=requirements,
            # The ORIGINAL aim, never `used_filters`. The Broadener relaxes titles
            # to salvage a zero-result search and can drift into a neighbouring job
            # family (a payroll search widening to "SAP Consultant"); screening
            # against what it relaxed TO would make broadening silently lower the
            # bar and rubber-stamp the drift. The role is the yardstick.
            target_titles=filters.get("currentJobTitles") or [],
            requested_location=req_location,
        )
        dropped = [v for v in verdicts if v.get("decision") == "drop"]
        if dropped:
            logger.info(
                "[Discover] %s/%s pre-screen kept %d of %d — skipped enriching %d "
                "off-role hit(s), e.g. %s",
                pipeline_id, job_id, len(cand_ids), len(verdicts), len(dropped),
                "; ".join(f"{v.get('title')!r}" for v in dropped[:3]),
            )
        await _record_prescreen(pipeline_id, job_id, verdicts)

        # ── Sourcing QA audit — does the KEPT set genuinely match the query? ──
        # Location leaks are already gone (the deterministic gate rejected
        # wrong-country hits above); this LLM pass catches the fuzzy residue —
        # an off-specialty profile the keyword channel let through (SAP FICO in
        # an SAP HCM search). It FLAGS, never deletes, and reports to the admin
        # QA page. Fail-open. Uses the stronger QA_AUDITOR_MODEL.
        if _settings_qa_enabled() and cand_ids:
            try:
                loc_rejected = sum(
                    1 for v in verdicts
                    if (v.get("location") or {}).get("decision") == "country_mismatch")
                await _audit_sourcing_results(
                    pipeline_id, job_id, filters, requirements, cand_ids, loc_rejected)
            except Exception as exc:  # noqa: BLE001 — QA never fails discovery
                logger.warning("[Discover] %s/%s sourcing QA error: %s",
                               pipeline_id, job_id, exc)

        # ── Shortfall: the tool NEVER widens the specialty on its own. When
        # the exact-specialty pool is thinner than the target, say so and offer
        # the Strategist's adjacent-specialty titles as opt-in chips — the
        # recruiter's click is the only thing that turns one into a search
        # term. A thin-but-honest list with a clear "here's how to widen" beats
        # a full list padded with the wrong profession.
        from app.config import settings as _settings

        kept = len(cand_ids)
        target = max(1, int(_settings.SOURCING_TARGET_CANDIDATES))
        shortfall = None
        if kept < target:
            shortfall = {
                "found": kept,
                "target": target,
                "adjacentTitles": list(adjacent_titles or []),
                "attempts": len(attempts),
                "reason": (
                    "No candidates matched this exact specialty."
                    if kept == 0 else
                    f"Only {kept} candidate(s) matched this exact specialty."
                ),
                "at": now,
            }

        await _finish(
            pipeline_id, job_id,
            # Zero kept = the recruiter must decide the next move (widen, edit,
            # rerun) — that is awaiting_input, not a bare "completed" that the
            # UI would render as a dead empty table.
            status="completed" if kept else "awaiting_input",
            lastSearchedAt=now, searchError=None, searchShortfall=shortfall,
        )
        # Counts (per-job AND the pipeline rollup the list UI reads) come from
        # the shared recount — this path used to set candidateCount only, which
        # left every Apify-sourced pipeline showing "0 candidates".
        await recount_pipeline(pipeline_id)
        logger.info("[Discover] %s/%s stored %d candidate(s) after %d attempt(s)%s",
                    pipeline_id, job_id, kept, len(attempts),
                    f" · shortfall (target {target})" if shortfall else "")

        # Deep enrichment is HUMAN-CONTROLLED, not automatic. Discovery shows
        # the recruiter the short profiles (name, current title, company,
        # location, photo) it found and STOPS — the paid Apify profile scrape
        # that pulls full work history/skills/education runs only when the
        # recruiter reviews the list and presses Enrich (→ enqueue_job_enrich).
        # `ready` = candidates are in and awaiting that decision.
        await _set_enrich(
            pipeline_id, job_id,
            "ready" if cand_ids else "none",
            enrichError=None,
            enrichReady=len(cand_ids),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[Discover] %s/%s failed: %s", pipeline_id, job_id, exc, exc_info=True)
        try:
            await _finish(pipeline_id, job_id, status="failed", searchError=str(exc)[:300])
        except Exception:
            pass
