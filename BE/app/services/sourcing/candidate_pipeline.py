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
from app.services.sourcing import apify_health
from app.services.sourcing.apollo_service import ApolloService
from app.services.sourcing.location_resolver import resolve_search_country

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
        apollo_filters = entry.get("lastApolloFilters")
        engines = entry.get("lastEngines")

        # Job sourced through the unified flow → replay the SAME combined search.
        if engines or apollo_filters:
            asyncio.create_task(_combined_discover_for_job(
                pipeline_id, job_id, filters or {}, apollo_filters or {},
                engines or {"apify": bool(filters), "apollo": bool(apollo_filters)},
                max_items, hints=hints, ladder=ladder, anchor=anchor,
                adjacent_titles=adjacent,
            ))
            return {"queued": True, "engine": "combined"}

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

    pipeline = await pipelines_col.find_one({"_id": oid}, {"jobs.jobId": 1, "tenantId": 1})
    if not pipeline:
        return {}
    now = datetime.utcnow()

    # Denormalize the pipeline's tenantId onto its candidates. Candidates are
    # inserted across several discovery paths that don't carry the tenant; this is
    # the one choke point every writer funnels through, so stamping here keeps all
    # candidate queries a single tenant-scoped filter. No-op for admin/unmapped
    # pipelines (no tenantId) and for already-stamped rows.
    tid = pipeline.get("tenantId")
    if tid:
        await candidates_col.update_many(
            {"pipelineId": pipeline_id, "tenantId": {"$ne": tid}},
            {"$set": {"tenantId": tid}},
        )

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


async def _finish(pipeline_id: str, job_id: str, *, status: str,
                  status_field: str = "searchStatus", **extras):
    """Write a job's terminal search status. ``status_field`` lets the combined
    runner target a per-engine status (``apifySearchStatus`` /
    ``apolloSearchStatus``) instead of the shared rollup ``searchStatus``."""
    pipelines_col = await get_collection("candidatePipelines")
    fields = {
        f"jobs.$.{status_field}": status,
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


# ── Apollo questionnaire discovery (search-only, no auto-enrich) ─────────────
#
# The second sourcing engine, chosen from the "Discover candidates" source
# picker. Where Apify runs a LinkedIn scrape and auto-enriches deep profiles,
# this runs Apollo's FREE people-search from the recruiter's structured filters
# (titles / locations / seniorities / industries / key-skills-as-q_keywords) and
# stores the results directly — contact info stays masked until the recruiter
# reveals it on demand (the existing per-candidate / bulk enrich). Candidates are
# stamped ``source="apollo_search"`` so they land in the same list as Apify ones
# but are distinguishable, and so on-demand enrich routes them through Apollo
# /people/match (never the Apify scrape).


async def _claim_discover_apollo(pipeline_id: str, job_id: str) -> bool:
    """Atomic → searchStatus 'running' from any non-running state, stamped with
    the Apollo engine. False if a search is already in flight for this job."""
    pipelines_col = await get_collection("candidatePipelines")
    res = await pipelines_col.update_one(
        {"_id": ObjectId(pipeline_id),
         "jobs": {"$elemMatch": {"jobId": job_id, "searchStatus": {"$ne": "running"}}}},
        {"$set": {"jobs.$.searchStatus": "running", "jobs.$.searchError": None,
                  "jobs.$.searchEngine": "apollo",
                  "jobs.$.lastSearchedAt": datetime.utcnow(), "updatedAt": datetime.utcnow()}},
    )
    return res.modified_count > 0


async def enqueue_apollo_discover(
    pipeline_id: str, job_id: str, filters: Dict[str, Any], max_items: int = 25,
) -> Dict[str, Any]:
    """Kick off Apollo questionnaire discovery for a job (background).

    ``filters`` is the questionnaire payload (titles / locations / skills /
    seniorities / industries). Poll the job's ``searchStatus``; results are
    search-only, so there is no ``enrichStatus`` phase to wait on. Raises
    ``ValueError("job_not_found")`` if the job isn't in the pipeline.
    """
    pipelines_col = await get_collection("candidatePipelines")
    res = await pipelines_col.update_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id},
        {"$set": {
            # Persist what this search ran with, mirroring the Apify path, so a
            # rerun could replay the SAME Apollo search.
            "jobs.$.lastApolloFilters": filters,
            "jobs.$.lastApolloMaxItems": max_items,
            "updatedAt": datetime.utcnow(),
        }},
    )
    if res.matched_count == 0:
        raise ValueError("job_not_found")
    asyncio.create_task(_apollo_discover_for_job(
        pipeline_id, job_id, filters, max_items,
    ))
    return {"queued": True}


async def _apollo_discover_for_job(
    pipeline_id: str, job_id: str, filters: Dict[str, Any], max_items: int,
    *, managed: bool = False,
) -> Optional[int]:
    """Background worker: one Apollo people-search from the questionnaire filters,
    stored as candidates. Metered under the candidate-sourcing stage.

    Errors are converted to ``searchStatus = failed`` so a crash never breaks the
    event loop or other pipelines.

    ``managed`` = driven by the combined runner: write the per-engine
    ``apolloSearchStatus`` (not the shared rollup), skip the shared claim and the
    final ``recount_pipeline`` (the runner owns both), and RETURN the kept count
    (None on failure) so the runner can compute the rollup.
    """
    from app.services.operations import cost_service

    sfield = "apolloSearchStatus" if managed else "searchStatus"
    efield = "apolloSearchError" if managed else "searchError"

    try:
        if not managed and not await _claim_discover_apollo(pipeline_id, job_id):
            logger.info("[Apollo] %s/%s already running — skip", pipeline_id, job_id)
            return None

        pipelines_col = await get_collection("candidatePipelines")
        pipeline = await pipelines_col.find_one({"_id": ObjectId(pipeline_id)})
        if not pipeline:
            return
        company_name = pipeline.get("companyName") or ""
        company_domain = pipeline.get("companyDomain") or ""

        titles = filters.get("titles") or []
        target_title = titles[0] if titles else ""

        apollo = ApolloService()
        async with cost_service.cost_context(
            cost_service.STAGE_CANDIDATE, pipelineId=pipeline_id, jobId=job_id,
        ):
            result = await asyncio.to_thread(
                apollo.search_people,
                titles=titles,
                locations=filters.get("locations"),
                skills=filters.get("skills"),
                seniorities=filters.get("seniorities"),
                industries=filters.get("industries"),
                max_results=max_items,
            )
        people = result.get("people", [])

        # ── post-filters: same-company drop + skip previously rejected ────────
        candidates_col = await get_collection("candidates")
        rejected_set = set(await candidates_col.distinct(
            "apolloId", {"pipelineId": pipeline_id, "isAccepted": False}))

        kept_people: List[dict] = []
        for p in people:
            if not p.get("id"):
                continue
            if _is_same_company(p, company_name, company_domain):
                continue
            if p["id"] in rejected_set:
                continue
            kept_people.append(p)

        # ── quality gates (parity with the Apify path): location + prescreen ──
        # Apollo results used to bypass BOTH gates — that is exactly how the
        # "Ruhr, 4720 Kelmis, Belgium" candidate slipped into a Germany search.
        # Now every Apollo hit runs the same deterministic country gate and the
        # same title prescreen the Apify path does, so accuracy is engine-agnostic.
        from app.config import settings as _settings
        from app.services.sourcing import location_resolver as _locres, prescreen_service

        requirements: Dict[str, Any] = {}
        try:
            from app.database import get_database
            from app.services.sourcing import role_spec_service
            spec = await role_spec_service.get_or_create_for_job(
                await get_database(), job_id)
            requirements = (spec or {}).get("requirements") or {}
        except Exception as exc:  # noqa: BLE001 — no spec ⇒ screen() keeps everything
            logger.warning("[Apollo] %s/%s no role spec for pre-screen: %s",
                           pipeline_id, job_id, exc)
        # Fold the Apollo key skills in as must-haves so the prescreen still has a
        # yardstick when the job has no parsed role spec.
        if filters.get("skills") and not requirements.get("mustHaveSkills"):
            requirements = {**requirements, "mustHaveSkills": list(filters["skills"])}
        gate_on = (_settings.SOURCING_LOCATION_GATE or "off").lower() == "country"
        req_location = _locres.requested_location(filters, requirements)

        # ── insert / append ───────────────────────────────────────────────────
        now = datetime.utcnow()
        inserted = 0
        re_surfaced = 0
        for p in kept_people:
            score, reasons = _score_match(p, target_title, None)
            doc = _build_candidate_doc(
                p,
                pipeline_id=pipeline_id,
                job_id=job_id,
                applied_industry_fallback=False,
                match_score=score,
                match_reasons=reasons,
                now=now,
            )
            # Source tag: keeps Apollo results in the same list but distinguishable
            # from Apify ones, and routes on-demand enrich through Apollo (not Apify).
            doc["source"] = "apollo_search"
            doc["sourceChannels"] = ["apollo"]

            # Gate 1: location (deterministic) — wrong COUNTRY is rejected outright.
            loc_verdict = None
            if gate_on and req_location:
                loc_verdict = _locres.location_verdict(req_location, doc.get("location"))
            if loc_verdict and loc_verdict["decision"] == "country_mismatch":
                doc["isAccepted"] = False
                doc["rejectionReason"] = f"Location mismatch — {loc_verdict['reason']}"
                doc["locationMismatch"] = True
                doc["decidedAt"] = now
                doc["matchScore"] = 0
                doc["matchReasons"] = [f"Location gate: {loc_verdict['reason']}"]
                doc["prescreen"] = {
                    "decision": "drop", "score": 0.0, "roleFit": 0.0,
                    "matchedVia": None, "location": loc_verdict,
                    "reasons": [f"Location gate: {loc_verdict['reason']}"],
                    "at": now, "channels": ["apollo"],
                }
            else:
                # Gate 2: title prescreen (same heuristic as the Apify path).
                if _settings.PRESCREEN_ENABLED:
                    keep, verdict = prescreen_service.screen(
                        {"currentTitle": doc.get("currentTitle")},
                        requirements=requirements, target_titles=titles,
                        min_score=_settings.PRESCREEN_MIN_SCORE,
                    )
                else:
                    keep, verdict = True, {"decision": "keep", "score": None,
                                           "reasons": ["Pre-screen disabled."]}
                verdict = {**verdict, "at": now, "channels": ["apollo"]}
                if loc_verdict:
                    verdict["location"] = loc_verdict
                    if loc_verdict["decision"] == "region_mismatch":
                        doc["locationFlag"] = loc_verdict["reason"]
                _cap_region_mismatch(verdict, loc_verdict, req_location)
                doc["prescreen"] = verdict
                if verdict.get("score") is not None:
                    doc["matchScore"] = int(round(float(verdict["score"])))
                    doc["matchReasons"] = list(verdict.get("reasons") or [])[:3]
                if not keep:
                    doc["isAccepted"] = False
                    doc["rejectionReason"] = (
                        verdict["reasons"][0] if verdict.get("reasons") else "Pre-screened out")
                    doc["decidedAt"] = now

            try:
                await candidates_col.insert_one(doc)
                inserted += 1
            except DuplicateKeyError:
                await candidates_col.update_one(
                    {"pipelineId": pipeline_id, "apolloId": p["id"]},
                    {
                        "$addToSet": {"sourceJobIds": job_id},
                        "$push": {"runHistory": {
                            "runAt": now, "jobId": job_id, "isRerun": False,
                            "appliedIndustryFallback": False,
                        }},
                        "$set": {"updatedAt": now},
                    },
                )
                re_surfaced += 1

        kept = inserted + re_surfaced
        finish_extras = {efield: None, "apolloKept": kept} if managed \
            else {"searchError": None}
        await _finish(pipeline_id, job_id, status="completed",
                      status_field=sfield, **finish_extras)
        if not managed:
            counts = await recount_pipeline(pipeline_id)
            total = counts.get("totalCandidates", 0)
        else:
            total = kept
        logger.info(
            "[Apollo] %s/%s done — found=%d inserted=%d re_surfaced=%d total=%d%s",
            pipeline_id, job_id, len(people), inserted, re_surfaced, total,
            " (managed)" if managed else "",
        )
        return kept if managed else None
    except Exception as exc:
        logger.error("[Apollo] %s/%s crashed: %s", pipeline_id, job_id, exc, exc_info=True)
        try:
            await _finish(pipeline_id, job_id, status="failed",
                          status_field=sfield, **{efield: str(exc)[:300]})
        except Exception:
            pass
        return None


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
    candidate in the job. Apify-only (deep profile scrape: work history,
    skills, headline) — see `_enrich_for_job` for why Apollo is not an option
    here.

    Returns ``{"queued": True}``; raises ``ValueError("job_not_found")`` if the
    job isn't in the pipeline.
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
    """Enrich a job's candidates via Apify — the only engine used here.

    Apify sourcing is the sole discovery engine now (Apollo search was dropped
    entirely), so every candidate's ``apolloId`` field actually holds a
    LinkedIn URN, not a real Apollo person id. Apollo's ``/people/match`` only
    matches on its OWN internal id, so calling it for these candidates is a
    guaranteed, 100%-of-the-time failure — confirmed live, 2026-07-31 ("Sales
    Manager SAP Retail": 30/30 Apollo failures, 0 Apify attempts, because the
    recruiter had picked the Apollo-only enrich option). Apollo also returns a
    materially thinner profile than Apify's deep scrape (no work history, no
    skills) even on the rare candidate it could match. So this function no
    longer offers an engine choice — it always runs the Apify deep-profile
    scrape, keyed off the candidate's LinkedIn URL.

    The returned summary keeps the ``apollo_enriched``/``apollo_failed`` keys
    (always 0) so the UI's existing result-shape reader doesn't need to change.
    """
    from app.services.sourcing.candidate_enrichment import enrich_candidates

    def _normalize_apify(s: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "selected": s.get("selected", 0),
            "apollo_enriched": 0, "apollo_failed": 0,
            "apify_enriched": s.get("enriched", 0),
            "cached": s.get("cached", 0),
            "not_found": s.get("not_found", 0),
            "skipped": s.get("skipped", 0),
        }

    if candidate_ids:
        return _normalize_apify(await enrich_candidates(candidate_ids=candidate_ids))
    return _normalize_apify(
        await enrich_candidates(pipeline_id=pipeline_id, job_id=job_id))


async def _regate_locations_after_enrich(
    pipeline_id: str, job_id: str, candidate_ids: Optional[List[str]],
) -> int:
    """Re-run the deterministic country gate once REAL locations are known.

    Apollo's people-search returns no location, so its search-time gate is a
    no-op — the wrong-country reject can only happen after enrichment fills in
    the city/country. This closes the "Kelmis, Belgium in a Germany search" leak
    for Apollo (and mops up any Apify residue). Only CONFIRMED-foreign rows are
    rejected; anything unresolved is left kept. Fail-open."""
    from app.config import settings as _settings
    from app.services.sourcing import location_resolver as _locres
    if (_settings.SOURCING_LOCATION_GATE or "off").lower() != "country":
        return 0
    pipelines_col = await get_collection("candidatePipelines")
    doc = await pipelines_col.find_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id}, {"jobs.$": 1})
    entry = (doc or {}).get("jobs", [{}])[0]
    filters = entry.get("lastDiscoverFilters") or entry.get("lastApolloFilters") or {}
    req_location = _locres.requested_location(filters, None)
    if not req_location:
        return 0

    candidates_col = await get_collection("candidates")
    scope: Dict[str, Any] = {"pipelineId": pipeline_id}
    if candidate_ids:
        scope["_id"] = {"$in": [ObjectId(c) for c in candidate_ids]}
    else:
        scope["sourceJobIds"] = job_id
    # Only rows still in the running — never re-judge an already-rejected one.
    scope["isAccepted"] = {"$ne": False}

    rejected = 0
    now = datetime.utcnow()
    async for c in candidates_col.find(scope, {"location": 1}):
        verdict = _locres.location_verdict(req_location, c.get("location"))
        if verdict.get("decision") != "country_mismatch":
            continue
        await candidates_col.update_one(
            {"_id": c["_id"]},
            {"$set": {
                "isAccepted": False,
                "locationMismatch": True,
                "rejectionReason": f"Location mismatch — {verdict['reason']}",
                "matchScore": 0,
                "decidedAt": now,
                "updatedAt": now,
            }},
        )
        rejected += 1
    if rejected:
        logger.info("[Enrich] %s/%s post-enrich location gate rejected %d wrong-country",
                    pipeline_id, job_id, rejected)
        await recount_pipeline(pipeline_id)
    return rejected


# ── Apify discovery: search questionnaire → candidates → auto-enrich ────────


def _cap_region_mismatch(verdict: Dict[str, Any], loc_verdict: Optional[Dict[str, Any]],
                         requested_location: Optional[str]) -> None:
    """Cap the prescreen score (in place) for right-country/wrong-region hits.

    They stay KEPT — relocation/remote is legitimate and a false drop is
    unrecoverable — but the table's score is how the recruiter reads overall
    fit, and a Berlin profile wearing 100 on a Bamberg search destroys trust in
    every other number. The cap (SOURCING_REGION_MISMATCH_CAP) drops them below
    genuinely in-region candidates in the default sort, and the appended reason
    says exactly why."""
    from app.config import settings
    cap = float(settings.SOURCING_REGION_MISMATCH_CAP or 0)
    if not cap or not loc_verdict or loc_verdict.get("decision") != "region_mismatch":
        return
    score = verdict.get("score")
    if score is None or float(score) <= cap:
        return
    verdict["score"] = cap
    reasons = list(verdict.get("reasons") or [])
    where = f" ({requested_location})" if requested_location else ""
    reasons.append(f"Outside the requested region{where} — score capped at {cap:g}.")
    verdict["reasons"] = reasons


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
    *, start_page: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """One PAID Apify search → parsed short profiles. Metered by the caller's stage.

    ``start_page`` resumes past pages already fetched for the SAME filters —
    see `apify_search_service._build_input` for why this always forces
    segmentation off.
    """
    from app.services.sourcing.apify_search_service import get_apify_search_service, parse_short_profile
    from app.services.operations import cost_service

    async with cost_service.cost_context(
        cost_service.STAGE_CANDIDATE, pipelineId=pipeline_id, jobId=job_id,
    ):
        service = get_apify_search_service()
        items = await asyncio.to_thread(
            service.search, filters, max_items=max_items, start_page=start_page)
    return [p for p in (parse_short_profile(i) for i in items) if p and p.get("profileId")]


def _keyword_channel_filters(filters: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The keyword-only variant of a filter set, or None when it adds nothing.

    LinkedIn headlines are self-description: the SAP HCM consultant whose
    headline says "IT-Consultant bei X" is invisible to a title-filtered search
    but IS found by the fuzzy keyword channel, because the actor's searchQuery
    matches profile keywords, not just the title line. This is one of the
    signals LinkedIn's own search uses and a title-only search silently loses.

    Two ways a search can carry a title requirement to drop, handled
    separately because the strategist redesign (2026-07-31) retired the first:

    1. The legacy shape — `currentJobTitles`/`pastJobTitles` populated as their
       own actor filter. The variant drops them and keeps everything else
       (locations, languages, enum filters, exclusions).
    2. The current shape — both of those are always empty; the title
       requirement lives inside searchQuery's own `(DOMAIN) AND (TITLE)`
       Boolean instead (see `strategist.py`). Until this was added, THIS path
       always returned None — `_keyword_channel_filters` had no title filter
       left to drop, so the keyword channel silently stopped running for
       every AI-built search the day of that redesign (confirmed live,
       2026-08-01: `searchAttempts[0].channelCounts` carried only `{"title":
       N}`, no `"keyword"` key, ever). The fix drops the TITLE GROUP from the
       query itself via `strategist.domain_only_query`, which only returns a
       value for a clean, unambiguous two-group Boolean — never guesses.

    Returns None when neither path applies (no titles to drop either way) or
    there's no query to search by.
    """
    q = (filters.get("searchQuery") or "").strip()
    if not q:
        return None
    if filters.get("currentJobTitles") or filters.get("pastJobTitles"):
        return {k: v for k, v in filters.items()
                if k not in ("currentJobTitles", "pastJobTitles")}
    from app.services.sourcing.strategist import domain_only_query
    domain_only = domain_only_query(q)
    if not domain_only:
        return None
    return {**filters, "searchQuery": domain_only}


def _primary_channel_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
    """The ACTUAL filters sent to Apify for the primary (title) channel.

    Confirmed live 2026-08-02 (Mirko Muller, linkedin.com/in/mirkomueller):
    the actor's `searchQuery` is fuzzy relevance search, not a strict boolean
    filter — its own docs call it "general search query (fuzzy search)",
    while `currentJobTitles` is documented as "filter-based ... deterministic
    results". Packing the title requirement into `searchQuery` (the
    2026-07-31 redesign's `(DOMAIN) AND (TITLE)` shape) means the title half
    is never actually enforced — a profile whose title shares ONE word with
    the domain phrase (Mirko's "Business Development Manager Retail" shares
    "Retail" with "SAP Retail") can score high enough on relevance alone to
    surface with zero real domain connection, confirmed reproducible at
    maxItems=50 on two separate days.

    This derives, WITHOUT touching the strategist's own SearchFilters or its
    `currentJobTitles=[]` invariant (many other consumers — the Judge payload,
    the Broadener's locked target, the QA auditor's title fallback — depend on
    that invariant staying exactly as the strategist produced it): a REAL
    `currentJobTitles` filter for the actual search call, using the same
    title-shaped-phrase extraction already used elsewhere
    (`_title_gate_titles_from_query`), and narrows `searchQuery` to the
    DOMAIN GROUP alone (`domain_only_query`) so the fuzzy half only ever has
    to do the one job it's suited for — finding domain evidence anywhere in
    the profile, not gatekeeping the title.

    Falls back to returning `filters` UNCHANGED — zero behavior change from
    today — whenever there's nothing safe to derive: `currentJobTitles`/
    `pastJobTitles` already populated (a manual/legacy search; don't
    override a recruiter's own explicit filter), or `searchQuery` isn't a
    clean, unambiguous two-group Boolean. Never guesses.
    """
    if filters.get("currentJobTitles") or filters.get("pastJobTitles"):
        return filters
    q = (filters.get("searchQuery") or "").strip()
    if not q:
        return filters
    from app.services.sourcing.strategist import domain_only_query, _title_gate_titles_from_query
    domain_only = domain_only_query(q)
    if not domain_only:
        return filters
    titles = _title_gate_titles_from_query(q)
    if not titles:
        return filters
    return {**filters, "searchQuery": domain_only, "currentJobTitles": titles}


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
    # A search carries a real title requirement either the old way
    # (`currentJobTitles` populated) or the current way (the boolean
    # `searchQuery` itself ANDs a title group onto the domain group — the
    # strategist redesign always leaves `currentJobTitles` empty and puts
    # titles here instead). Labeling every AND-anchored search "keyword" once
    # `currentJobTitles` went permanently empty is what let
    # `_channel_screen_policy`'s keyword-channel rescue (below) waive the
    # title/seniority screen for 100% of candidates instead of the minority
    # it was written for — confirmed live (2026-08-01): a compound query
    # returned 28 students/trainees out of 30 under the old labeling.
    has_title_requirement = bool(filters.get("currentJobTitles")) or (
        " AND " in str(filters.get("searchQuery") or "").upper()
    )
    primary = "title" if has_title_requirement else "keyword"
    primary_filters = _primary_channel_filters(filters)
    profiles = await _run_search(pipeline_id, job_id, primary_filters, max_items)
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


def is_domain_anchored(filters: Optional[Dict[str, Any]]) -> bool:
    """True when the search itself carried the domain, so every hit is evidenced.

    The actor's ``searchQuery`` is matched against the profile's FULL TEXT, not
    the title line. When we send a real one, LinkedIn has already verified
    something we cannot see locally — Short mode returns only title, company and
    location. A single word derived from a title ("Account") is not that, so we
    require either a boolean expression or a genuine multi-word phrase.
    """
    from app.services.sourcing.strategist import _is_boolean_query

    q = str((filters or {}).get("searchQuery") or "").strip()
    if not q:
        return False
    return _is_boolean_query(q) or len(q.split()) >= 2


def _provisional_score(
    base: float, *, title: str, company: str, channels: List[str],
    candidate_titles: Optional[List[str]] = None,
    domain_terms: Optional[List[str]] = None,
) -> Tuple[float, List[str]]:
    """Rank a hit we cannot yet verify, using only the free fields.

    Rescued hits used to be floored at a flat 30, which is fine while it applies
    to a handful of them and useless once it applies to most of the list — every
    candidate lands in one bucket and the recruiter's sort stops meaning
    anything. These four signals are all derivable from the short profile, so
    ranking costs nothing and stays deterministic.
    """
    from app.services.sourcing import prescreen_service as _ps

    score = base
    reasons: List[str] = []
    t_tokens = _ps.tokens(title or "")

    if candidate_titles and t_tokens:
        for ct in candidate_titles:
            if _ps._phrase_overlap(t_tokens, str(ct)) >= 0.5:
                score += 30.0
                reasons.append(f"Title “{title}” looks like “{ct}”.")
                break

    if len(channels) > 1:
        # Found independently by the title AND the keyword search — the strongest
        # signal available before enrichment.
        score += 15.0
        reasons.append("Found by both the title and the keyword search.")

    if company and domain_terms:
        c = company.lower()
        hit = next((d for d in domain_terms if d and str(d).lower() in c), None)
        if hit:
            score += 10.0
            reasons.append(f"Employer “{company}” carries the domain term “{hit}”.")

    return min(95.0, score), reasons


def _channel_screen_policy(
    keep: bool, verdict: Dict[str, Any], channels: List[str], *, title: str = "",
    company: str = "", domain_anchored: bool = False,
    candidate_titles: Optional[List[str]] = None,
    domain_terms: Optional[List[str]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Channel-aware adjustments to the title-only prescreen verdict.

    Freelance/self-employed and owner/executive titles are hard policy
    rejections — unconditional, regardless of how the hit was found. They are
    NOT softened by domain evidence: a query that matches profile text can't
    tell us someone stopped being a freelancer.

    Everyone else: a hit whose TITLE shares no vocabulary with the role is not
    a random stranger when the search carried the domain — the actor matched
    the query against the profile's own content ("IT-Consultant bei X" whose
    profile says SAP HCM). The title-only gate can't see that evidence, so it
    must not be allowed to drop on it — false DROPs are unrecoverable, a false
    KEEP costs one $0.004 scrape and the matcher catches it a minute later.

    ``domain_anchored`` widens that reprieve from keyword-channel hits to EVERY
    hit, because in a domain-anchored search the domain was AND-ed into both
    channels. This is what lets a "Principal Consultant" who sells SAP Retail
    survive: his title carries no signal at all, and under the title-only rule
    he was dropped before anyone could look at him.

    Corroboration: a hit found independently by BOTH the title search and the
    keyword search is the strongest pre-enrichment signal there is — it gets a
    rank bonus so it sorts above single-channel hits.
    """
    from app.services.sourcing import prescreen_service as _ps

    is_fl, fl_reason = _ps.is_freelance_or_self_employed(title=title, company=company)
    if is_fl or verdict.get("isFreelance"):
        return False, {
            **verdict, "decision": "drop", "isFreelance": True, "score": 0.0,
            "reasons": [
                fl_reason or "Candidate is marked as freelance / self-employed — rejected by policy.",
                *(verdict.get("reasons") or []),
            ],
        }

    evidenced = domain_anchored or "keyword" in channels

    if not keep and evidenced:
        # The keyword/domain channel matches profile TEXT, so it pulls in
        # owners and executives whose profile merely name-drops the tool (the
        # "cofounder shows up as an SAP CO consultant" leak). Refuse the rescue
        # for them: an owner/founder/Geschäftsführer is running a business, not
        # doing the hands-on specialty. A genuine title match never reaches
        # here (it was already kept), so this only ever drops a non-matching
        # executive — unconditionally, domain evidence included.
        if _ps.is_executive_title(title):
            return False, {
                **verdict, "decision": "drop",
                "reasons": [
                    f"Title “{title}” is an owner/executive role, not the "
                    "hands-on specialty — only the keyword/domain match found it.",
                    *(verdict.get("reasons") or []),
                ],
            }
        keep = True
        score, extra = _provisional_score(
            30.0, title=title, company=company, channels=channels,
            candidate_titles=candidate_titles, domain_terms=domain_terms)
        verdict = {
            **verdict, "decision": "keep", "score": score,
            "reasons": [
                "Profile content matches the search keywords — kept for "
                "enrichment even though the title alone doesn't show it.",
                *extra, *(verdict.get("reasons") or []),
            ],
        }
        return keep, verdict

    if keep and len(channels) > 1 and verdict.get("score") is not None:
        verdict = {**verdict, "score": min(95.0, float(verdict["score"]) + 5.0)}
    return keep, verdict


async def rescreen_freelance_candidates(pipeline_id: Optional[str] = None, job_id: Optional[str] = None) -> int:
    """Pass over candidates collection and reject any candidate whose company/title/headline is freelance or self-employed."""
    from app.services.sourcing import prescreen_service as _ps
    candidates_col = await get_collection("candidates")
    scope: Dict[str, Any] = {"isAccepted": {"$ne": False}}
    if pipeline_id:
        scope["pipelineId"] = pipeline_id
    if job_id:
        scope["sourceJobIds"] = job_id

    rejected = 0
    now = datetime.utcnow()
    async for c in candidates_col.find(scope, {"currentTitle": 1, "currentCompany": 1, "company": 1, "headline": 1}):
        title = c.get("currentTitle") or ""
        company = c.get("currentCompany") or c.get("company") or ""
        headline = c.get("headline") or ""
        is_fl, fl_reason = _ps.is_freelance_or_self_employed(title=title, company=company, headline=headline)
        if is_fl:
            await candidates_col.update_one(
                {"_id": c["_id"]},
                {"$set": {
                    "isAccepted": False,
                    "rejectionReason": fl_reason,
                    "matchScore": 0,
                    "decidedAt": now,
                    "updatedAt": now,
                }},
            )
            rejected += 1
    if rejected and pipeline_id:
        logger.info("[Prescreen] %s/%s freelance policy gate rejected %d freelance/self-employed candidates",
                    pipeline_id, job_id, rejected)
        await recount_pipeline(pipeline_id)
    return rejected


async def _run_job_enrich(
    pipeline_id: str, job_id: str, candidate_ids: Optional[List[str]],
) -> None:
    """Background worker: enrich the selected candidates (or all in the job)."""
    from app.services.operations import cost_service
    try:
        await _set_enrich(pipeline_id, job_id, "running", enrichError=None)
        async with cost_service.cost_context(
            cost_service.STAGE_CANDIDATE, pipelineId=pipeline_id, jobId=job_id,
        ):
            summary = await _enrich_for_job(pipeline_id, job_id, candidate_ids)
        # Now that real locations are known, reject confirmed wrong-country hits
        # (the Apollo location gate can only run here). Fail-open.
        try:
            await _regate_locations_after_enrich(pipeline_id, job_id, candidate_ids)
            await rescreen_freelance_candidates(pipeline_id, job_id)
        except Exception as exc:  # noqa: BLE001 — gate must never fail enrichment
            logger.warning("[Enrich] %s/%s post-enrich gate error: %s",
                           pipeline_id, job_id, exc)
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
        await apify_health.alert_if_actionable(
            exc, where=f"candidate enrichment (pipeline {pipeline_id}, job {job_id})",
            extra={"pipelineId": pipeline_id, "jobId": job_id},
        )


# A "3 for 3 real, confirmed" pattern is real evidence, not proof it holds
# for every profile — demote hard, never hard-reject on it. Mirrors the
# multiplier scale already used for confirmed-risky signals downstream in
# the matcher (`matching_service._FUNCTION_MISMATCH_MULTIPLIER`).
_SPECIALIZATION_ONLY_DEMOTION = 0.3


def _apply_domain_evidence_demotion(
    keep: bool, verdict: Dict[str, Any], *, title: str, domain_query: str,
) -> Tuple[bool, Dict[str, Any]]:
    """Free, pre-enrichment demotion using only the short-profile title.

    See `prescreen_service.domain_evidence_signal` for the full finding.
    Only the "specialization_only" pattern is demoted — it's the one
    signature actually confirmed live (Mirko Muller, Hendrik Jansen, Dieter
    Kosancic, all 2026-08-02). Every other outcome, INCLUDING "neither" (the
    majority case — every real genuine match that day showed neither word in
    their current title), is left untouched: there is no free-data basis to
    treat "neither" as anything but unknown, and doing so would demote the
    same population that today's real good candidates came from.

    Never changes `keep` — a false DROP here is unrecoverable, and "3 for 3"
    is real but not a certainty for every future case.
    """
    if not domain_query:
        return keep, verdict
    from app.services.sourcing.prescreen_service import domain_evidence_signal

    signal = domain_evidence_signal(title, domain_query)
    verdict = {**verdict, "domainEvidenceSignal": signal}
    if signal != "specialization_only":
        return keep, verdict
    score = verdict.get("score")
    if score is not None:
        verdict["score"] = round(float(score) * _SPECIALIZATION_ONLY_DEMOTION, 1)
    verdict["reasons"] = [
        "Title mentions the specialty but no SAP/ecosystem connection anywhere "
        "in it — a pattern confirmed to often be a false match; ranked low, "
        "not dropped, pending enrichment.",
        *(verdict.get("reasons") or []),
    ]
    return keep, verdict


async def _store_profiles(
    profiles: List[Dict[str, Any]], *, pipeline_id: str, job_id: str,
    search_query: str, now: datetime,
    requirements: Optional[Dict[str, Any]] = None,
    target_titles: Optional[List[str]] = None,
    requested_location: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
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
    from app.services.sourcing import location_resolver, prescreen_service

    candidates_col = await get_collection("candidates")
    cand_ids: List[str] = []
    verdicts: List[Dict[str, Any]] = []
    gate_on = (settings.SOURCING_LOCATION_GATE or "off").lower() == "country"

    # When the search itself carried the domain, LinkedIn matched it against each
    # profile's FULL TEXT — evidence the title-only screen below cannot see and
    # must therefore not overrule.
    req = requirements or {}
    domain_anchored = is_domain_anchored(filters or {"searchQuery": search_query})
    candidate_titles = list(req.get("candidateTitles") or []) or list(target_titles or [])
    domain_terms = list(req.get("domainTerms") or [])
    if domain_anchored:
        logger.info("[Discover] %s/%s domain-anchored search — title screen ranks, "
                    "does not reject", pipeline_id, job_id)

    # Free, pre-enrichment demotion signal (2026-08-02) — see
    # `prescreen_service.domain_evidence_signal`'s docstring for the full
    # finding. Reuses the SAME domain-only extraction already used for the
    # primary search channel, so this checks against exactly what was
    # actually searched for, not a separately-maintained term list.
    from app.services.sourcing.strategist import domain_only_query
    _raw_query = (filters or {}).get("searchQuery") or search_query or ""
    domain_query_text = domain_only_query(_raw_query) or ""

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

        # ── Gate 2: title & company pre-screen ──
        if settings.PRESCREEN_ENABLED:
            keep, verdict = prescreen_service.screen(
                p, requirements=requirements, target_titles=target_titles,
                min_score=settings.PRESCREEN_MIN_SCORE,
            )
            keep, verdict = _channel_screen_policy(
                keep, verdict, channels,
                title=p.get("currentTitle") or "",
                company=p.get("currentCompany") or p.get("company") or "",
                domain_anchored=domain_anchored,
                candidate_titles=candidate_titles,
                domain_terms=domain_terms,
            )
            keep, verdict = _apply_domain_evidence_demotion(
                keep, verdict, title=p.get("currentTitle") or "",
                domain_query=domain_query_text,
            )
        else:
            keep, verdict = True, {"decision": "keep", "score": None,
                                   "reasons": ["Pre-screen disabled."]}
        verdict = {**verdict, "at": now, "channels": channels}
        if loc_verdict:
            verdict["location"] = loc_verdict
            if loc_verdict["decision"] == "region_mismatch":
                doc["locationFlag"] = loc_verdict["reason"]
        _cap_region_mismatch(verdict, loc_verdict, requested_location)
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
) -> Dict[str, Any]:
    """Run the sourcing auditor over the kept candidates and record the report.

    Builds the auditor's view from the stored candidate rows (title/company/
    channels) so it audits exactly what the recruiter will see. The auditor now
    HIDES high-confidence off-specialty results, so a recount follows any
    rejection. Returns the QA summary (``{}`` if there was nothing to audit)."""
    from app.services.sourcing import sourcing_qa_service

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
        return {}

    # What the auditor judges against. The POSTING title is deliberately not the
    # anchor: for a commercial role it describes the opening ("SAP Retail
    # Consultant"), not the person, and judging an "Account Executive" against it
    # matches the auditor's own reject clause — "a different profession that
    # merely name-drops a tool" — which re-imported the title filter at the very
    # last gate, after the search had correctly found them.
    # filters["currentJobTitles"] is always empty now (2026-07-31 redesign: the
    # Strategist folds titles into searchQuery's TITLE GROUP instead of this
    # field, so it never double-restricts the actor-side search). The anchor
    # falls back to extracting those same titles back out of searchQuery before
    # ever reaching the raw posting title.
    from app.services.sourcing.strategist import _title_gate_titles_from_query
    candidate_titles = (requirements.get("candidateTitles")
                        or filters.get("currentJobTitles")
                        or _title_gate_titles_from_query(filters.get("searchQuery") or "")
                        or [])
    query = {
        "title": (candidate_titles[0] if candidate_titles
                  else requirements.get("title")),
        "targetTitles": list(candidate_titles),
        "mustHaveSkills": requirements.get("mustHaveSkills") or [],
        "seniority": requirements.get("seniority"),
        "roleFamily": requirements.get("roleFamily"),
        "domainTerms": requirements.get("domainTerms") or [],
    }
    from app.database import get_database
    summary = await sourcing_qa_service.audit_results(
        await get_database(),
        pipeline_id=pipeline_id, job_id=job_id,
        jd_title=query["title"] or "", query=query,
        kept=kept, location_rejected=location_rejected,
        domain_anchored=is_domain_anchored(filters),
    )
    if summary.get("rejected"):
        logger.info("[Discover] %s/%s sourcing QA HID %d wrong-specialty result(s)",
                    pipeline_id, job_id, summary["rejected"])
        # Hidden candidates change the accepted totals — recount so the header
        # and the list agree.
        try:
            await recount_pipeline(pipeline_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Discover] %s/%s recount after QA reject failed: %s",
                           pipeline_id, job_id, exc)
    elif summary.get("mismatchesFlagged"):
        logger.info("[Discover] %s/%s sourcing QA flagged %d off-specialty result(s)",
                    pipeline_id, job_id, summary["mismatchesFlagged"])
    return summary


async def _audit_combined_results(
    pipeline_id: str, job_id: str,
    apify_filters: Dict[str, Any], apollo_filters: Dict[str, Any],
) -> None:
    """QA the MERGED kept set (both engines) in one pass.

    Restores the guarantee the dual-engine push broke: EVERY candidate a recruiter
    sees — Apollo included — goes through the adversarial sourcing auditor, not
    just the Apify half. Fail-open; QA never fails a discovery run."""
    if not _settings_qa_enabled():
        return
    candidates_col = await get_collection("candidates")
    cand_ids: List[str] = []
    location_rejected = 0
    try:
        async for d in candidates_col.find(
            {"pipelineId": pipeline_id, "sourceJobIds": job_id},
            {"isAccepted": 1, "locationMismatch": 1}):
            if d.get("locationMismatch"):
                location_rejected += 1
            # Rows the gate (or recruiter) rejected carry isAccepted=False and
            # are not part of the visible list, so they need no QA.
            if d.get("isAccepted") is not False:
                cand_ids.append(str(d["_id"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Combined] %s/%s QA gather failed: %s", pipeline_id, job_id, exc)
        return
    if not cand_ids:
        return

    requirements: Dict[str, Any] = {}
    try:
        from app.database import get_database
        from app.services.sourcing import role_spec_service
        spec = await role_spec_service.get_or_create_for_job(
            await get_database(), job_id)
        requirements = (spec or {}).get("requirements") or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Combined] %s/%s QA no role spec: %s", pipeline_id, job_id, exc)

    # Judge against BOTH engines' title families and fold Apollo skills in as a
    # must-have fallback, so the auditor sees the full target.
    titles = list(dict.fromkeys(
        (apify_filters.get("currentJobTitles") or [])
        + (apollo_filters.get("titles") or [])))
    merged_filters = {**apify_filters, "currentJobTitles": titles}
    if apollo_filters.get("skills") and not requirements.get("mustHaveSkills"):
        requirements = {**requirements, "mustHaveSkills": list(apollo_filters["skills"])}

    try:
        await _audit_sourcing_results(
            pipeline_id, job_id, merged_filters, requirements,
            cand_ids, location_rejected)
    except Exception as exc:  # noqa: BLE001 — QA never fails discovery
        logger.warning("[Combined] %s/%s combined QA error: %s", pipeline_id, job_id, exc)


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

    Cost is bounded four ways: ``SOURCING_MAX_BROADEN_ATTEMPTS`` caps the retries,
    the Broadener refuses to repeat a filter set it already tried, a structural
    repeat-check below stops the loop if a proposal still duplicates an earlier
    attempt (a clamped field can make two proposals converge), and it stops
    early once the filters are broad enough that zero means "not on LinkedIn".
    The Broadener may relax enums/companies/language ONLY — the titles, query
    AND locations are clamped in code to the recruiter-approved values
    (``broadener.lock_target``), so drifting into a neighbouring profession or
    a different geography is structurally impossible. A thin market surfaces as
    an honest short/empty result the recruiter can consciously widen.
    """
    from app.config import settings
    from app.services.sourcing.apify_profile_service import ApifyRunFailed
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
        proposed = decision.filters.to_search_input()
        # Structural repeat-guard: the clamp (titles/query/locations) can collapse
        # a proposal into a set already tried — e.g. a persisted ladder step whose
        # only change was widening the location. Re-running an identical search is
        # a guaranteed-zero paid call, so stop instead.
        if any(proposed == a.filters for a in attempts):
            logger.info("[Discover] %s/%s broadening proposal repeats attempt filters "
                        "after clamping — stopping", pipeline_id, job_id)
            return [], attempts, current
        current = proposed
        # The recruiter's own exclude-seniority choice is a manual, deliberate
        # signal (never AI-set — see strategist._ENUM_INFERRED_FIELDS) and must
        # survive broadening the same way titles/query/locations do
        # (lock_target, above). SearchFilters.excludeSeniorityLevel stays a
        # single-value, AI-facing field on purpose — widening it would just
        # grow the Broadener's own LLM's chance to mis-set it — so the
        # recruiter's real list is restored directly onto the dict that
        # actually reaches the actor, bypassing that model entirely.
        if filters.get("excludeSeniorityLevel"):
            current["excludeSeniorityLevel"] = filters["excludeSeniorityLevel"]
        action, reasoning = decision.action, decision.reasoning


# ── Pagination: get more of the SAME query, never a relaxed one ─────────────
#
# Precision fixes alone don't create more candidates from a genuinely niche
# specialty — they just show a cleaner slice of what's already there. Getting
# MORE means looking further into the same ranked result list (paging), not
# widening the ask. `_meta.pagination` (confirmed live 2026-08-02) tells us
# exactly how many pages exist for a query, so this never has to guess when
# to stop.
_PAGE_FETCH_CAP = 5  # hard ceiling PER RUN — a later, recruiter-triggered
# "search more" gets its own fresh 5-page allowance starting past whatever
# was already fetched, never a lifetime cap.

# The recruiter's own framing: "our ideal percentage of valid candidates...
# needs to be eighty percentage of fifty" — a fraction of what was actually
# REQUESTED (`max_items`), not the pre-existing, unrelated
# `SOURCING_TARGET_CANDIDATES` setting (default 10, whose job is triggering
# the "widen your specialty" shortfall banner — a different concept).
_PAGINATION_TARGET_FRACTION = 0.8


def _count_free_verified(verdicts: List[Dict[str, Any]]) -> int:
    """How many KEPT candidates pass the free, pre-enrichment domain-evidence
    check — i.e. were not demoted for the one pattern actually confirmed live
    (specialization word alone, no ecosystem word — see
    `prescreen_service.domain_evidence_signal`). This is the free proxy the
    pagination loop targets; the paid, full-profile verification
    (`matching_service.domain_evidence_fit`) still only ever runs later, when
    a recruiter chooses to enrich — paging never spends enrichment budget on
    its own."""
    return sum(
        1 for v in verdicts
        if v.get("decision") != "drop" and v.get("domainEvidenceSignal") != "specialization_only"
    )


def _next_pagination_page(
    *, verified_count: int, target_count: int, pages_fetched: int,
    total_pages: Optional[int], page_cap: int = _PAGE_FETCH_CAP,
) -> Optional[int]:
    """The next page number to fetch, or ``None`` to stop — three independent
    conditions, whichever is true first:

      1. The target is already met.
      2. `total_pages` is exhausted — LinkedIn genuinely has no more results
         for this exact query; fetching "page N+1" past that would just
         re-request the same last page. `total_pages=None` (never learned
         yet, e.g. an empty first page) is treated as "unknown, don't stop
         on this basis alone" — only an actual known total can end the loop
         this way.
      3. `page_cap` — the hard, predictable cost ceiling, independent of
         whether the target was ever reachable for this specific query.
    """
    if verified_count >= target_count:
        return None
    if pages_fetched >= page_cap:
        return None
    if total_pages is not None and pages_fetched >= total_pages:
        return None
    return pages_fetched + 1


def pagination_stop_reason(
    *, verified_count: int, target_count: int, pages_fetched: int,
    total_pages: Optional[int], page_cap: int = _PAGE_FETCH_CAP,
) -> str:
    """Which of the three stop conditions actually applies, for honest
    reporting — mirrors `_next_pagination_page`'s own checks exactly, so the
    reported reason can never disagree with why the loop actually stopped."""
    if verified_count >= target_count:
        return "target_reached"
    if total_pages is not None and pages_fetched >= total_pages:
        return "pages_exhausted"
    if pages_fetched >= page_cap:
        return "page_cap_reached"
    return "stopped"  # defensive only — every real call site hits one of the above


async def _paginate_primary_channel_to_target(
    pipeline_id: str, job_id: str, used_filters: Dict[str, Any], max_items: int,
    *, initial_verdicts: List[Dict[str, Any]], initial_pagination: Optional[Dict[str, Any]],
    requirements: Dict[str, Any], target_titles: List[str],
    requested_location: Optional[str],
) -> Dict[str, Any]:
    """After the initial search+screen, fetch MORE pages of the SAME primary
    channel (title filter + domain-only query, unchanged) if the free-verified
    count falls short of the target — by paging deeper into the same ranked
    result list, never by relaxing anything. Reuses `_store_profiles` for
    every new page, so new candidates persist exactly like the first batch
    (dedup by apolloId is already handled there).

    Returns a summary dict: pagesFetched, totalPages, totalElements,
    verifiedCount, stopReason, allVerdicts (initial + every page fetched
    here) — `stopReason`/`totalPages` are what let the caller report an
    honest total (only when `pagesFetched >= totalPages`, i.e. genuinely
    everyone was looked at) instead of overstating coverage.
    """
    target = max(1, round(_PAGINATION_TARGET_FRACTION * max_items))
    all_verdicts = list(initial_verdicts)
    all_cand_ids: List[str] = []
    verified = _count_free_verified(all_verdicts)
    total_pages = (initial_pagination or {}).get("totalPages")
    total_elements = (initial_pagination or {}).get("totalElements")
    pages_fetched = 1  # the initial search already fetched page 1

    while True:
        next_page = _next_pagination_page(
            verified_count=verified, target_count=target,
            pages_fetched=pages_fetched, total_pages=total_pages,
        )
        if next_page is None:
            break
        try:
            primary_filters = _primary_channel_filters(used_filters)
            new_profiles = await _run_search(
                pipeline_id, job_id, primary_filters, max_items, start_page=next_page,
            )
        except Exception as exc:  # noqa: BLE001 — pagination is an enhancement, never fatal
            logger.warning("[Discover] %s/%s pagination page %d failed: %s",
                           pipeline_id, job_id, next_page, exc)
            break
        pages_fetched = next_page
        if not new_profiles:
            total_pages = pages_fetched  # confirms there was nothing more here
            break
        for p in new_profiles:
            p["channels"] = ["title"]
            pg = p.get("pagination") or {}
            if pg.get("totalPages") is not None:
                total_pages = pg["totalPages"]
            if pg.get("totalElements") is not None:
                total_elements = pg["totalElements"]

        page_cand_ids, page_verdicts = await _store_profiles(
            new_profiles, pipeline_id=pipeline_id, job_id=job_id,
            search_query=(used_filters.get("searchQuery") or ""), now=datetime.utcnow(),
            requirements=requirements, target_titles=target_titles,
            requested_location=requested_location, filters=used_filters,
        )
        all_cand_ids.extend(page_cand_ids)
        all_verdicts.extend(page_verdicts)
        verified = _count_free_verified(all_verdicts)

    stop_reason = pagination_stop_reason(
        verified_count=verified, target_count=target,
        pages_fetched=pages_fetched, total_pages=total_pages,
    )
    return {
        "pagesFetched": pages_fetched, "totalPages": total_pages,
        "totalElements": total_elements, "verifiedCount": verified,
        "targetCount": target, "stopReason": stop_reason,
        "allVerdicts": all_verdicts, "newCandidateIds": all_cand_ids,
    }


async def _discover_candidates_for_job(
    pipeline_id: str, job_id: str, filters: Dict[str, Any], max_items: int,
    *,
    auto_broaden: bool = False,
    hints: Optional[Dict[str, Any]] = None,
    ladder: Optional[List[Dict[str, Any]]] = None,
    anchor: Optional[Dict[str, Any]] = None,
    adjacent_titles: Optional[List[str]] = None,
    managed: bool = False,
) -> Optional[int]:
    """Background: search LinkedIn via Apify (title + keyword channels), store
    the results as candidates ranked by role relevance, and stop for the
    recruiter's enrichment decision.

    With ``auto_broaden`` the search is agentic: a zero-result attempt is retried
    with filters the Broadener relaxes based on what already failed, instead of
    handing the recruiter an empty list. When the search still comes up short of
    ``SOURCING_TARGET_CANDIDATES``, the job carries a ``searchShortfall`` payload
    offering the Strategist's adjacent-specialty titles as recruiter-opt-in
    chips — the tool never widens the specialty on its own.

    ``managed`` = driven by the combined runner: write the per-engine
    ``apifySearchStatus`` (not the shared rollup), skip the shared claim, the
    final ``recount_pipeline`` and the enrich-status write (the runner owns them),
    and RETURN the kept count (None on failure) so the runner can roll up.
    """
    sfield = "apifySearchStatus" if managed else "searchStatus"
    efield = "apifySearchError" if managed else "searchError"

    if not managed and not await _claim_discover(pipeline_id, job_id):
        logger.info("[Discover] %s/%s already running — skip", pipeline_id, job_id)
        return None

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
            from app.services.sourcing import role_spec_service

            spec = await role_spec_service.get_or_create_for_job(await get_database(), job_id)
            requirements = (spec or {}).get("requirements") or {}
        except Exception as exc:  # noqa: BLE001 — no spec ⇒ screen() keeps everything
            logger.warning("[Discover] %s/%s no role spec for pre-screen: %s",
                           pipeline_id, job_id, exc)

        # The location the recruiter ASKED for — the original filters, not the
        # broadened ones (the Broadener may relax location as a last resort; the
        # gate must judge against the recruiter's actual instruction).
        from app.services.sourcing import location_resolver as _locres
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
            # The ORIGINAL filters too — `is_domain_anchored` reads searchQuery to
            # decide whether the title screen may reject or may only rank.
            filters=filters,
        )

        # Page deeper into this SAME query (never a relaxed one) when the
        # free-verified count falls short of target — see
        # `_paginate_primary_channel_to_target`. A kill switch, not a tuning
        # knob: `SOURCING_PAGINATION_ENABLED=False` reverts to today's
        # single-page behavior with zero other change.
        from app.config import settings as _pagination_settings

        pagination_summary: Optional[Dict[str, Any]] = None
        if _pagination_settings.SOURCING_PAGINATION_ENABLED and cand_ids:
            title_hit = next((p for p in profiles if "title" in (p.get("channels") or [])), None)
            initial_pagination = (title_hit or {}).get("pagination")
            try:
                pagination_summary = await _paginate_primary_channel_to_target(
                    pipeline_id, job_id, used_filters, max_items,
                    initial_verdicts=verdicts, initial_pagination=initial_pagination,
                    requirements=requirements, target_titles=filters.get("currentJobTitles") or [],
                    requested_location=req_location,
                )
            except Exception as exc:  # noqa: BLE001 — an enhancement, never fatal to discovery
                logger.warning("[Discover] %s/%s pagination failed, keeping first page: %s",
                               pipeline_id, job_id, exc)
            if pagination_summary:
                cand_ids = cand_ids + pagination_summary["newCandidateIds"]
                verdicts = pagination_summary["allVerdicts"]

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
        #
        # Under the combined runner (``managed``) the QA pass is DEFERRED to the
        # runner so it audits the MERGED Apify+Apollo set in one call — otherwise
        # Apollo results (often the majority) would never be QA-verified, which
        # is exactly the trust regression the dual-engine push introduced.
        if _settings_qa_enabled() and cand_ids and not managed:
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

        # Honest coverage reporting (2026-08-02): only claim the real total
        # when every page of it was actually looked at (`pagesFetched >=
        # totalPages`) — otherwise we only sampled the top-ranked slice and
        # showing "X total" would imply completeness the search doesn't have.
        # `totalElements`/`totalPages` themselves are real, live-confirmed
        # actor data (`_meta.pagination`), not an estimate.
        search_coverage = None
        if pagination_summary and pagination_summary.get("totalPages") is not None:
            search_coverage = {
                "totalElements": pagination_summary.get("totalElements"),
                "totalPages": pagination_summary["totalPages"],
                "pagesFetched": pagination_summary["pagesFetched"],
                "fullyCovered": pagination_summary["pagesFetched"] >= pagination_summary["totalPages"],
                "verifiedCount": pagination_summary.get("verifiedCount"),
                "stopReason": pagination_summary.get("stopReason"),
            }

        finish_extras: Dict[str, Any] = {
            "lastSearchedAt": now, "searchShortfall": shortfall,
            "searchCoverage": search_coverage,
        }
        finish_extras[efield] = None
        if managed:
            finish_extras["apifyKept"] = kept
        await _finish(
            pipeline_id, job_id,
            # Zero kept = the recruiter must decide the next move (widen, edit,
            # rerun) — that is awaiting_input, not a bare "completed" that the
            # UI would render as a dead empty table. In managed mode the combined
            # runner owns that rollup, so the engine status is just found/none.
            status=("completed" if kept else ("no_results" if managed else "awaiting_input")),
            status_field=sfield, **finish_extras,
        )
        if not managed:
            # Counts (per-job AND the pipeline rollup the list UI reads) come from
            # the shared recount — this path used to set candidateCount only, which
            # left every Apify-sourced pipeline showing "0 candidates".
            await recount_pipeline(pipeline_id)
        logger.info("[Discover] %s/%s stored %d candidate(s) after %d attempt(s)%s%s",
                    pipeline_id, job_id, kept, len(attempts),
                    f" · shortfall (target {target})" if shortfall else "",
                    " (managed)" if managed else "")

        # Deep enrichment is HUMAN-CONTROLLED, not automatic. Discovery shows
        # the recruiter the short profiles (name, current title, company,
        # location, photo) it found and STOPS — the paid Apify profile scrape
        # that pulls full work history/skills/education runs only when the
        # recruiter reviews the list and presses Enrich (→ enqueue_job_enrich).
        # `ready` = candidates are in and awaiting that decision. In managed mode
        # the combined runner sets enrich status once both engines have settled.
        if not managed:
            await _set_enrich(
                pipeline_id, job_id,
                "ready" if cand_ids else "none",
                enrichError=None,
                enrichReady=len(cand_ids),
            )
        return kept if managed else None
    except Exception as exc:  # noqa: BLE001
        logger.error("[Discover] %s/%s failed: %s", pipeline_id, job_id, exc, exc_info=True)
        try:
            await _finish(pipeline_id, job_id, status="failed",
                          status_field=sfield, **{efield: str(exc)[:300]})
        except Exception:
            pass
        # Tell the operator only if this is a wall they must clear (dead token,
        # exhausted plan). Deduped by cause, so one bad token does not send one
        # email per job.
        await apify_health.alert_if_actionable(
            exc, where=f"candidate discovery (pipeline {pipeline_id}, job {job_id})",
            extra={"pipelineId": pipeline_id, "jobId": job_id},
        )
        return None


# ── Combined discovery: Apify + Apollo concurrently, merged (Phase 5) ────────
#
# The unified "Run search" fires BOTH engines at once from one screen. Each
# engine runs its own managed worker (writing its per-engine sub-status), then
# this runner dedups the overlap by LinkedIn URL and rolls the two sub-statuses
# up into the shared ``searchStatus`` the candidate list already polls.


# Regional LinkedIn hosts (de.linkedin.com, www.linkedin.com…) all point at the
# same profile — normalise them away so a both-engines hit collapses to one row.
_LINKEDIN_HOST = re.compile(r"^https?://([a-z0-9-]+\.)?linkedin\.com", re.I)


def _norm_linkedin_url(url: Optional[str]) -> str:
    """Comparable key for a LinkedIn profile URL (host/scheme/query stripped)."""
    if not url:
        return ""
    u = url.strip().lower()
    u = _LINKEDIN_HOST.sub("linkedin.com", u)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    return u


async def _dedupe_cross_engine(pipeline_id: str, job_id: str) -> int:
    """Collapse candidates that BOTH engines surfaced into one row.

    Apify keys a candidate by LinkedIn profile id, Apollo by Apollo person id, so
    the same person found by both is two rows with the same
    ``externalLinkedinUrl``. Keep the Apify row (LinkedIn-native — photo, better
    title parse), union ``sourceChannels``, record the other engine in
    ``alsoFoundVia`` and carry the Apollo id in ``apolloPersonId`` so a later
    contact reveal can still use it, then delete the duplicate. Only ever
    deletes, never rewrites ``apolloId`` — the (pipelineId, apolloId) unique
    index stays intact. Returns the number of rows merged away.
    """
    candidates_col = await get_collection("candidates")
    cur = candidates_col.find(
        {"pipelineId": pipeline_id, "sourceJobIds": job_id,
         "externalLinkedinUrl": {"$nin": [None, ""]}},
        {"externalLinkedinUrl": 1, "source": 1, "apolloId": 1,
         "sourceChannels": 1},
    )
    groups: Dict[str, List[dict]] = {}
    async for d in cur:
        key = _norm_linkedin_url(d.get("externalLinkedinUrl"))
        if key:
            groups.setdefault(key, []).append(d)

    now = datetime.utcnow()
    merged = 0
    for docs in groups.values():
        if len(docs) < 2:
            continue
        # Apify row first (source == apify_search), else keep the first seen.
        docs.sort(key=lambda d: 0 if d.get("source") == "apify_search" else 1)
        keeper, dups = docs[0], docs[1:]
        channels = set(keeper.get("sourceChannels") or [])
        also: set[str] = set()
        apollo_pid: Optional[str] = None
        for d in dups:
            channels.update(d.get("sourceChannels") or [])
            if d.get("source"):
                also.add(d["source"])
            if d.get("source") == "apollo_search" and d.get("apolloId"):
                apollo_pid = d["apolloId"]
        set_fields: Dict[str, Any] = {"updatedAt": now}
        if channels:
            set_fields["sourceChannels"] = sorted(c for c in channels if c)
        also.discard(keeper.get("source") or "")
        if also:
            set_fields["alsoFoundVia"] = sorted(also)
        if apollo_pid and keeper.get("source") == "apify_search":
            set_fields["apolloPersonId"] = apollo_pid
        await candidates_col.update_one({"_id": keeper["_id"]}, {"$set": set_fields})
        await candidates_col.delete_many({"_id": {"$in": [d["_id"] for d in dups]}})
        merged += len(dups)
    if merged:
        logger.info("[Combined] %s/%s deduped %d cross-engine duplicate(s)",
                    pipeline_id, job_id, merged)
    return merged


# "The vendor refused on a billing/plan limit", NOT "no candidates matched" —
# surfacing that difference is the whole point of the rollup fix, since a plan
# limit must never read as an empty talent pool. The marker list moved to
# apify_health: this was the third of three copies that disagreed with each
# other, so the verdict depended on which layer saw the error first.
def _is_quota_error(msg: Optional[str]) -> bool:
    if not msg:
        return False
    return apify_health.is_spend_error(msg)


async def _engine_errors(pipeline_id: str, job_id: str) -> Dict[str, Optional[str]]:
    """The per-engine error strings the managed workers recorded on the job."""
    pipelines_col = await get_collection("candidatePipelines")
    doc = await pipelines_col.find_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id}, {"jobs.$": 1})
    job = ((doc or {}).get("jobs") or [{}])[0]
    return {"apify": job.get("apifySearchError"),
            "apollo": job.get("apolloSearchError")}


async def _claim_combined(
    pipeline_id: str, job_id: str, run_apify: bool, run_apollo: bool,
) -> bool:
    """Atomic → rollup ``searchStatus`` running, with per-engine sub-statuses.
    False if a search is already running for this job."""
    pipelines_col = await get_collection("candidatePipelines")
    res = await pipelines_col.update_one(
        {"_id": ObjectId(pipeline_id),
         "jobs": {"$elemMatch": {"jobId": job_id, "searchStatus": {"$ne": "running"}}}},
        {"$set": {
            "jobs.$.searchStatus": "running",
            "jobs.$.searchEngine": "combined",
            "jobs.$.searchError": None,
            "jobs.$.apifySearchStatus": "running" if run_apify else "skipped",
            "jobs.$.apolloSearchStatus": "running" if run_apollo else "skipped",
            "jobs.$.apifySearchError": None,
            "jobs.$.apolloSearchError": None,
            "jobs.$.apifyKept": None,
            "jobs.$.apolloKept": None,
            "jobs.$.lastSearchedAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }},
    )
    return res.modified_count > 0


async def _job_candidate_count(pipeline_id: str, job_id: str) -> int:
    pipelines_col = await get_collection("candidatePipelines")
    doc = await pipelines_col.find_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id}, {"jobs.$": 1})
    return ((doc or {}).get("jobs") or [{}])[0].get("candidateCount", 0)


async def enqueue_combined_discover(
    pipeline_id: str, job_id: str,
    apify_filters: Dict[str, Any], apollo_filters: Dict[str, Any],
    engines: Dict[str, bool], max_items: int = 25,
    *,
    hints: Optional[Dict[str, Any]] = None,
    ladder: Optional[List[Dict[str, Any]]] = None,
    anchor: Optional[Dict[str, Any]] = None,
    adjacent_titles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Kick off the unified Apify+Apollo discovery for a job (background).

    Persists BOTH filter sets and the engine toggles (so a rerun replays the same
    combined search), then runs the enabled engines concurrently. Poll the job's
    ``searchStatus`` (rollup) and, for the per-engine breakdown, ``apifySearchStatus``
    / ``apolloSearchStatus`` + ``apifyKept`` / ``apolloKept``.
    """
    pipelines_col = await get_collection("candidatePipelines")
    res = await pipelines_col.update_one(
        {"_id": ObjectId(pipeline_id), "jobs.jobId": job_id},
        {"$set": {
            "jobs.$.lastDiscoverFilters": apify_filters,
            "jobs.$.lastDiscoverMaxItems": max_items,
            "jobs.$.lastDiscoverHints": hints,
            "jobs.$.lastDiscoverLadder": ladder,
            "jobs.$.lastDiscoverAnchor": anchor,
            "jobs.$.adjacentTitles": adjacent_titles or [],
            "jobs.$.lastApolloFilters": apollo_filters,
            "jobs.$.lastApolloMaxItems": max_items,
            "jobs.$.lastEngines": engines,
            "updatedAt": datetime.utcnow(),
        }},
    )
    if res.matched_count == 0:
        raise ValueError("job_not_found")
    asyncio.create_task(_combined_discover_for_job(
        pipeline_id, job_id, apify_filters, apollo_filters, engines, max_items,
        hints=hints, ladder=ladder, anchor=anchor, adjacent_titles=adjacent_titles,
    ))
    return {"queued": True}


async def _combined_discover_for_job(
    pipeline_id: str, job_id: str,
    apify_filters: Dict[str, Any], apollo_filters: Dict[str, Any],
    engines: Dict[str, bool], max_items: int,
    *,
    hints: Optional[Dict[str, Any]] = None,
    ladder: Optional[List[Dict[str, Any]]] = None,
    anchor: Optional[Dict[str, Any]] = None,
    adjacent_titles: Optional[List[str]] = None,
) -> None:
    """Run the enabled engines concurrently, dedup, and roll their sub-statuses
    up into the shared ``searchStatus``. Owns the single claim/recount/enrich so
    the two managed workers never race on the rollup."""
    from app.services.operations import cost_service

    from app.config import settings as _settings
    run_apify = bool(engines.get("apify", True)) and bool(apify_filters)
    # Apollo is disabled for candidate search unless explicitly re-enabled — the
    # product searches LinkedIn only. Apollo stays for contact enrichment.
    run_apollo = (
        bool(_settings.SOURCING_APOLLO_SEARCH_ENABLED)
        and bool(engines.get("apollo", True)) and bool(apollo_filters))
    if not run_apify and not run_apollo:
        logger.info("[Combined] %s/%s no engine enabled — nothing to do",
                    pipeline_id, job_id)
        await _finish(pipeline_id, job_id, status="awaiting_input")
        return

    if not await _claim_combined(pipeline_id, job_id, run_apify, run_apollo):
        logger.info("[Combined] %s/%s already running — skip", pipeline_id, job_id)
        return

    try:
        # Preflight before spending anything: a dead token or an exhausted plan
        # is far cheaper to learn about here than after the run reports "no
        # candidates". Once per run, not per job, and never fatal — a preflight
        # that could block sourcing would be a worse bug than the one it warns
        # about.
        if run_apify:
            try:
                await apify_health.preflight(source="discovery run")
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Combined] Apify preflight skipped: %s", exc)

        names: List[str] = []
        coros = []
        if run_apify:
            names.append("apify")
            coros.append(_discover_candidates_for_job(
                pipeline_id, job_id, apify_filters, max_items,
                auto_broaden=True, hints=hints, ladder=ladder, anchor=anchor,
                adjacent_titles=adjacent_titles, managed=True,
            ))
        if run_apollo:
            names.append("apollo")
            coros.append(_apollo_discover_for_job(
                pipeline_id, job_id, apollo_filters, max_items, managed=True,
            ))

        # Metered as one candidate-sourcing unit spanning both engines' spend.
        async with cost_service.cost_context(
            cost_service.STAGE_CANDIDATE, pipelineId=pipeline_id, jobId=job_id,
        ):
            results = await asyncio.gather(*coros, return_exceptions=True)

        kept: Dict[str, Optional[int]] = {}
        for name, r in zip(names, results):
            if isinstance(r, Exception):
                logger.error("[Combined] %s/%s %s engine raised: %s",
                             pipeline_id, job_id, name, r)
                kept[name] = None
            else:
                kept[name] = r

        if run_apify and run_apollo:
            try:
                await _dedupe_cross_engine(pipeline_id, job_id)
            except Exception as exc:  # noqa: BLE001 — dedup must never fail the run
                logger.warning("[Combined] %s/%s dedup failed: %s",
                               pipeline_id, job_id, exc)

        # Single authoritative recount, then roll the sub-statuses up.
        await recount_pipeline(pipeline_id)

        # Judge success by what THIS run added, per-engine — never the job's
        # cumulative candidate count. A stale candidate from an earlier run used
        # to make a fully-failed re-run report "completed" (the exact bug: Apollo
        # off, Apify failed on its cap, header still said "completed · 1").
        ran = list(names)
        failed = [n for n in ran if kept.get(n) is None]
        this_run_added = sum(int(kept.get(n) or 0) for n in ran)

        engine_errors = await _engine_errors(pipeline_id, job_id)
        quota_hit = any(_is_quota_error(engine_errors.get(n)) for n in failed)

        if ran and len(failed) == len(ran):
            # Every engine that ran failed → the run failed, full stop.
            rollup = "failed"
        elif this_run_added > 0:
            rollup = "completed"
        else:
            rollup = "awaiting_input"

        # Surface an honest notice WHENEVER an engine failed — even on a partial
        # success — distinguishing a plan/credit limit from an empty result.
        err = None
        if failed:
            bits = []
            for n in failed:
                label = "LinkedIn" if n == "apify" else "Apollo"
                if _is_quota_error(engine_errors.get(n)):
                    bits.append(f"{label} hit its plan/credit limit — not an empty result")
                else:
                    bits.append(f"{label} search failed")
            err = "; ".join(bits)
        await _finish(pipeline_id, job_id, status=rollup,
                      lastSearchedAt=datetime.utcnow(), searchError=err,
                      searchNotice=err, searchQuotaHit=bool(quota_hit))

        # The rollup already classified the Apify failure to write searchQuotaHit;
        # until now that verdict only reached the database. Route it to the person
        # who can actually clear the wall.
        apify_error = engine_errors.get("apify")
        if apify_error and "apify" in failed:
            await apify_health.alert_if_actionable(
                apify_error,
                where=f"combined discovery rollup (pipeline {pipeline_id}, job {job_id})",
                extra={"pipelineId": pipeline_id, "jobId": job_id, "rollup": rollup},
            )

        # Enrich readiness follows the Apify (deep-scrape) side; Apollo is
        # search-only (contact revealed on demand).
        apify_kept = kept.get("apify") or 0
        await _set_enrich(
            pipeline_id, job_id,
            "ready" if apify_kept > 0 else "none",
            enrichError=None, enrichReady=apify_kept,
        )

        # ── Unified sourcing QA over the MERGED set (both engines) ────────────
        # The trust layer: every candidate the recruiter will see — Apollo
        # included — is verified by the adversarial auditor here, in one pass.
        # Fail-open, so a QA hiccup can never sink a completed search.
        if rollup == "completed":
            try:
                await _audit_combined_results(
                    pipeline_id, job_id, apify_filters, apollo_filters)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Combined] %s/%s QA pass failed: %s",
                               pipeline_id, job_id, exc)

        logger.info(
            "[Combined] %s/%s done — apify=%s apollo=%s added=%d rollup=%s%s",
            pipeline_id, job_id, kept.get("apify"), kept.get("apollo"),
            this_run_added, rollup, " (quota)" if quota_hit else "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[Combined] %s/%s crashed: %s", pipeline_id, job_id, exc, exc_info=True)
        try:
            await _finish(pipeline_id, job_id, status="failed",
                          searchError=str(exc)[:300])
        except Exception:
            pass
