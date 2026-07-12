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
        "logs": [],
        "progress": {"total": len(ids), "processed": 0, "considered": 0},
        "params": {"returnTop": return_top or settings.MATCH_RETURN_TOP},
        "createdAt": now,
        "updatedAt": now,
    }
    match_run_id = str((await db["match_runs"].insert_one(run_doc)).inserted_id)

    asyncio.create_task(
        _run_pipeline_match(match_run_id, pipeline_id, job_id, ids, jd_text, job_title, return_top)
    )
    return match_run_id


def _candidate_name(doc: Dict[str, Any]) -> str:
    """Best display name for a candidate doc (for the live log)."""
    name = (doc.get("displayName") or "").strip()
    if name:
        return name
    fn = (doc.get("firstName") or "").strip()
    ln = (doc.get("lastName") or "").strip()
    if ln in ("—", "-", "–", "--"):
        ln = ""
    full = f"{fn} {ln}".strip()
    return full or "Candidate"


async def _run_pipeline_match(
    match_run_id: str,
    pipeline_id: str,
    job_id: str,
    candidate_ids: List[str],
    jd_text: str,
    job_title: str,
    return_top: Optional[int],
) -> None:
    """Background worker — STREAMING.

    Instead of batch-enriching then scoring the whole set at the end, each
    candidate is pushed through a queue one at a time: enrich (skipped when
    already enriched) → embed → score → reason, then its result + a run of log
    lines are persisted immediately. The UI polls the run and renders each
    candidate the moment it lands, ranked live, rather than waiting for the
    batch to finish.
    """
    db = await get_database()
    runs_col = db["match_runs"]
    cands_col = db["candidates"]
    run_oid = ObjectId(match_run_id)
    return_top = return_top or settings.MATCH_RETURN_TOP

    logs: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    total = len(candidate_ids)
    processed = 0
    considered = 0

    async def flush(*, status: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> None:
        """Persist the current logs/results/progress snapshot (single writer, so a
        whole-array $set is safe and keeps the UI's ranked view consistent)."""
        update: Dict[str, Any] = {
            "logs": logs,
            "results": results,
            "candidatesConsidered": considered,
            "progress": {"total": total, "processed": processed, "considered": considered},
            "updatedAt": _now(),
        }
        if status:
            update["status"] = status
        if extra:
            update.update(extra)
        await runs_col.update_one({"_id": run_oid}, {"$set": update})

    async def log(message: str, level: str = "info", *, persist: bool = True) -> None:
        logs.append({"ts": _now().isoformat() + "Z", "message": message, "level": level})
        logger.info("[PipelineMatch] run %s · %s", match_run_id, message)
        if persist:
            await flush()

    def _rank_results() -> None:
        results.sort(key=lambda r: r["score"], reverse=True)
        del results[return_top:]  # keep only the top-N as they accumulate

    # Meter every billable call this run makes (embeddings, extraction,
    # reasoning, Apollo enrichment, Apify) under the "matching" stage.
    from app.services import cost_service
    _cost_cm = cost_service.cost_context(
        cost_service.STAGE_MATCHING, label=job_title,
        matchRunId=match_run_id, pipelineId=pipeline_id, jobId=job_id)
    await _cost_cm.__aenter__()
    try:
        from app.services.candidate_enrichment import bulk_enrich

        await log(f"Starting match · {total} candidate(s) queued")

        # Parse + embed the JD once up front.
        await log("Parsing job description…")
        requirements = await llm.parse_jd(jd_text)
        jd_vector = await embeddings.embed_text(jd_text[:8000])
        must = requirements.get("mustHaveSkills") or []
        await flush(extra={"requirements": requirements})
        await log(
            "Job description parsed"
            + (f" · {len(must)} must-have skill(s)" if must else ""),
        )

        # Walk the queue one candidate at a time.
        for cid in candidate_ids:
            try:
                doc = await cands_col.find_one({"_id": ObjectId(cid)})
            except Exception:
                doc = None
            if not doc:
                processed += 1
                await log(f"✗ Candidate {cid} not found — skipped", level="warn")
                continue

            name = _candidate_name(doc)
            already = bool(doc.get("isApifyEnriched") and (doc.get("apifyEnrichment") or {}).get("profile"))

            if already:
                await log(f"✓ {name} — already enriched, skipping enrichment")
            else:
                await log(f"Enriching {name} (Apollo → LinkedIn profile)…")
                try:
                    await bulk_enrich(candidate_ids=[cid])
                except Exception as e:  # noqa: BLE001 — one bad enrich mustn't kill the run
                    await log(f"⚠ {name} — enrichment error: {str(e)[:160]}", level="warn")
                doc = await cands_col.find_one({"_id": ObjectId(cid)}) or doc

            enrichment = doc.get("apifyEnrichment") or {}
            profile = enrichment.get("profile") or {}
            if not profile:
                processed += 1
                await log(f"✗ {name} — no LinkedIn profile found, excluded from scoring", level="warn")
                continue
            if not already:
                await log(f"✓ {name} enriched")

            # Embed → score → reason for this one candidate.
            await log(f"Scoring {name}…", persist=False)
            contact = enrichment.get("contact") or {}
            embed_text = _embed_text_from_profile(profile, profile.get("summary") or "")
            vector = await embeddings.embed_text(embed_text)
            sim = _cosine(jd_vector, vector)
            score, subscores, gaps = _score_candidate(requirements, profile, sim)
            scored = {"gaps": gaps}

            rid: Dict[str, Any] = {}
            try:
                resp = await llm.reason_candidates(requirements, [{
                    "id": cid,
                    "currentTitle": profile.get("currentTitle"),
                    "totalYears": profile.get("totalYears"),
                    "skills": profile.get("skills") or [],
                    "missingMustHave": gaps,
                }])
                for item in (resp.get("candidates") or []):
                    if str(item.get("id")) == cid:
                        rid = item
                        break
            except Exception:  # noqa: BLE001 — reasoning is best-effort
                logger.warning("[PipelineMatch] reasoning failed for %s; using deterministic", cid)

            reasons = rid.get("reasons") or _fallback_reasons(requirements, profile, scored)
            results.append({
                "candidateId": cid,
                "source": "pipeline",
                "fullName": profile.get("fullName") or doc.get("displayName"),
                "currentTitle": profile.get("currentTitle") or doc.get("currentTitle"),
                "location": profile.get("location") or doc.get("location"),
                "score": score,
                "subscores": subscores,
                "reasons": reasons,
                "gaps": rid.get("gaps") or gaps,
                "contact": {
                    "email": contact.get("email"),
                    "phone": contact.get("phone"),
                    "linkedin": contact.get("linkedin") or doc.get("externalLinkedinUrl"),
                },
            })
            considered += 1
            processed += 1
            _rank_results()
            await log(f"✓ {name} scored {score}")

        await flush(status="completed", extra={
            "modelVersions": {
                "extract": llm.extraction_version(),
                "embed": embeddings.embedding_version(),
                "reason": llm.reasoning_version(),
            },
        })
        await log(f"Done — matched {considered} of {total} candidate(s)")
        logger.info(
            "[PipelineMatch] run %s done — %d considered, top %d",
            match_run_id, considered, len(results),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[PipelineMatch] run %s failed: %s", match_run_id, exc, exc_info=True)
        try:
            logs.append({"ts": _now().isoformat() + "Z", "message": f"Run failed: {str(exc)[:200]}", "level": "error"})
            await runs_col.update_one(
                {"_id": run_oid},
                {"$set": {"status": "failed", "error": str(exc)[:300], "logs": logs, "updatedAt": _now()}},
            )
        except Exception:
            pass
    finally:
        await _cost_cm.__aexit__(None, None, None)
