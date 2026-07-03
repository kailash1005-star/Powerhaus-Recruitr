"""
Pipeline candidate matching — JD ↔ the job's enriched candidates.

Unlike ``matching_service.run_match`` (which matches a JD against the uploaded CV
corpus via the vector store), this matches a JD against a BOUNDED set of pipeline
candidates whose deep profiles came from Apify enrichment. Because the set is
small (the selected candidates for one job), we embed + score them in-memory —
no vector store needed.

Flow (background):
  1. Persist a ``match_runs`` doc with ``status:"running"`` immediately so the UI
     can poll it, and return its id.
  2. Auto-enrich any selected candidates that aren't Apify-enriched yet.
  3. Parse + embed the JD, embed each candidate profile, cosine-sim, score with
     the SAME deterministic scorer the CV engine uses, add LLM reasoning.
  4. Write the results onto the run and flip ``status:"completed"`` (or
     ``"failed"``).

Reuses matching_service / llm / embeddings helpers — no new scoring logic.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.config import settings
from app.database import get_database
from app.services import embedding_service as embeddings
from app.services import llm_extraction_service as llm
from app.services.matching_service import (
    _embed_text_from_profile,
    _fallback_reasons,
    _score_candidate,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.utcnow()


def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity of two equal-length vectors (0 when either is empty)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _resolve_jd_text(job: Dict[str, Any]) -> str:
    """The JD text to match against. Prefer the scraped description; when jobspy
    captured none (common — descriptions are optional), synthesise a minimal JD
    from the title + any structured requirements + location so matching still
    runs instead of hard-failing."""
    details = job.get("jobDetails") or {}
    desc = (details.get("description") or "").strip()
    if desc:
        return desc
    parts: List[str] = []
    if job.get("title"):
        parts.append(str(job["title"]))
    reqs = details.get("requirements")
    if isinstance(reqs, list) and reqs:
        parts.append("Requirements:\n" + "\n".join(f"- {r}" for r in reqs if r))
    if job.get("location"):
        parts.append(f"Location: {job['location']}")
    return "\n".join(parts).strip()


async def start_pipeline_match(
    *,
    pipeline_id: str,
    job_id: str,
    candidate_ids: Optional[List[str]] = None,
    return_top: Optional[int] = None,
) -> str:
    """Create a running match_run for this job's JD + selected candidates and
    kick off the background compute. Returns the match_run id.

    Raises ValueError if the job has no description or no candidates are selected.
    """
    db = await get_database()
    jobs_col = db["jobs"]
    pipelines_col = db["candidatePipelines"]

    job = await jobs_col.find_one({"_id": ObjectId(job_id)})
    if not job:
        raise ValueError("job_not_found")
    jd_text = _resolve_jd_text(job)
    if not jd_text:
        raise ValueError("job has no description or title to match against")

    pipeline = await pipelines_col.find_one({"_id": ObjectId(pipeline_id)})
    company_name = (pipeline or {}).get("companyName") or ""
    job_title = job.get("title") or ""

    # Resolve the selected candidate ids (default: every candidate in the job).
    cands_col = db["candidates"]
    if candidate_ids:
        ids = list(candidate_ids)
    else:
        ids = [
            str(d["_id"])
            async for d in cands_col.find(
                {"pipelineId": pipeline_id, "sourceJobIds": job_id}, {"_id": 1}
            )
        ]
    if not ids:
        raise ValueError("no candidates selected")

    now = _now()
    run_doc = {
        "source": "pipeline",
        "pipelineId": pipeline_id,
        "jobId": job_id,
        "status": "running",
        "jdTitle": job_title,
        "jdText": jd_text,
        "jdFileName": None,
        "companyName": company_name,
        "candidateIds": ids,
        "candidatesConsidered": 0,
        "results": [],
        "params": {"returnTop": return_top or settings.MATCH_RETURN_TOP},
        "createdAt": now,
        "updatedAt": now,
    }
    match_run_id = str((await db["match_runs"].insert_one(run_doc)).inserted_id)

    asyncio.create_task(
        _run_pipeline_match(match_run_id, pipeline_id, job_id, ids, jd_text, job_title, return_top)
    )
    return match_run_id


async def _run_pipeline_match(
    match_run_id: str,
    pipeline_id: str,
    job_id: str,
    candidate_ids: List[str],
    jd_text: str,
    job_title: str,
    return_top: Optional[int],
) -> None:
    """Background worker: enrich-if-needed, score, reason, persist."""
    db = await get_database()
    runs_col = db["match_runs"]
    run_oid = ObjectId(match_run_id)
    return_top = return_top or settings.MATCH_RETURN_TOP

    try:
        # 1. Auto-enrich any selected candidates that aren't Apify-enriched yet.
        from app.services.candidate_enrichment import bulk_enrich
        await bulk_enrich(candidate_ids=candidate_ids)

        # 2. Load the (now) enriched candidates.
        cands_col = db["candidates"]
        obj_ids = [ObjectId(cid) for cid in candidate_ids]
        docs: List[Dict[str, Any]] = [
            d async for d in cands_col.find({"_id": {"$in": obj_ids}})
        ]
        enriched = [
            d for d in docs
            if d.get("isApifyEnriched") and (d.get("apifyEnrichment") or {}).get("profile")
        ]

        # 3. Parse + embed the JD.
        requirements = await llm.parse_jd(jd_text)
        jd_vector = await embeddings.embed_text(jd_text[:8000])

        # 4. Embed + score each candidate.
        scored: List[Dict[str, Any]] = []
        for doc in enriched:
            enrichment = doc.get("apifyEnrichment") or {}
            profile = enrichment.get("profile") or {}
            contact = enrichment.get("contact") or {}
            embed_text = _embed_text_from_profile(profile, profile.get("summary") or "")
            vector = await embeddings.embed_text(embed_text)
            sim = _cosine(jd_vector, vector)
            score, subscores, gaps = _score_candidate(requirements, profile, sim)
            scored.append({
                "doc": doc, "profile": profile, "contact": contact,
                "score": score, "subscores": subscores, "gaps": gaps,
            })
        scored.sort(key=lambda x: x["score"], reverse=True)

        # 5. LLM reasoning for the top N.
        reason_n = min(settings.MATCH_REASON_TOP_N, len(scored))
        top = scored[:reason_n]
        anonymized = [{
            "id": str(s["doc"]["_id"]),
            "currentTitle": s["profile"].get("currentTitle"),
            "totalYears": s["profile"].get("totalYears"),
            "skills": s["profile"].get("skills") or [],
            "missingMustHave": s["gaps"],
        } for s in top]

        reasons_by_id: Dict[str, Dict[str, Any]] = {}
        if anonymized:
            try:
                resp = await llm.reason_candidates(requirements, anonymized)
                for item in (resp.get("candidates") or []):
                    if item.get("id"):
                        reasons_by_id[str(item["id"])] = item
            except Exception:  # noqa: BLE001 — reasoning is best-effort
                logger.warning("[PipelineMatch] reasoning failed; using deterministic reasons")

        # 6. Assemble final results (same shape the /matching UI renders).
        results: List[Dict[str, Any]] = []
        for s in top[:return_top]:
            doc = s["doc"]
            cid = str(doc["_id"])
            profile = s["profile"]
            contact = s["contact"]
            rid = reasons_by_id.get(cid, {})
            reasons = rid.get("reasons") or _fallback_reasons(requirements, profile, s)
            results.append({
                "candidateId": cid,
                "source": "pipeline",
                "fullName": profile.get("fullName") or doc.get("displayName"),
                "currentTitle": profile.get("currentTitle") or doc.get("currentTitle"),
                "location": profile.get("location") or doc.get("location"),
                "score": s["score"],
                "subscores": s["subscores"],
                "reasons": reasons,
                "gaps": rid.get("gaps") or s["gaps"],
                "contact": {
                    "email": contact.get("email"),
                    "phone": contact.get("phone"),
                    "linkedin": contact.get("linkedin") or doc.get("externalLinkedinUrl"),
                },
            })

        await runs_col.update_one(
            {"_id": run_oid},
            {"$set": {
                "status": "completed",
                "requirements": requirements,
                "candidatesConsidered": len(scored),
                "results": results,
                "modelVersions": {
                    "extract": llm.extraction_version(),
                    "embed": embeddings.embedding_version(),
                    "reason": llm.reasoning_version(),
                },
                "updatedAt": _now(),
            }},
        )
        logger.info(
            "[PipelineMatch] run %s done — %d considered, top %d",
            match_run_id, len(scored), len(results),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[PipelineMatch] run %s failed: %s", match_run_id, exc, exc_info=True)
        try:
            await runs_col.update_one(
                {"_id": run_oid},
                {"$set": {"status": "failed", "error": str(exc)[:300], "updatedAt": _now()}},
            )
        except Exception:
            pass
