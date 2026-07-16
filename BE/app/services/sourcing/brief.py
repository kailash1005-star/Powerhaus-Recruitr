"""Assemble a SearchBrief from what the database already knows about a job.

The recruiter should never retype what we have. This gathers the job title,
location, company context, and the best available JD text, then layers the
recruiter's optional hints on top.

JD text is preferred in this order:
  1. `parsed_jds.requirements` — already LLM-extracted for the matching engine.
     Structured and short: the cheapest, highest-signal input available.
  2. `parsed_jds.rawText` — the parsed document, if the structured pass is absent.
  3. `jobs.jobDetails.description` — the scraped/manual posting body.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bson import ObjectId

from app.database import get_collection
from app.services.sourcing.models import SearchBrief

logger = logging.getLogger(__name__)


def _requirements_text(req: Dict[str, Any]) -> str:
    """Render structured JD requirements back into prose for the agent."""
    parts = []
    if req.get("title"):
        parts.append(f"Title: {req['title']}")
    if req.get("seniority"):
        parts.append(f"Seniority: {req['seniority']}")
    if req.get("minYears") is not None:
        parts.append(f"Minimum years: {req['minYears']}")
    if req.get("location"):
        parts.append(f"Location: {req['location']}")
    if req.get("mustHaveSkills"):
        parts.append("Must-have skills: " + ", ".join(req["mustHaveSkills"]))
    if req.get("niceToHaveSkills"):
        parts.append("Nice-to-have skills: " + ", ".join(req["niceToHaveSkills"]))
    if req.get("responsibilities"):
        parts.append("Responsibilities:\n- " + "\n- ".join(req["responsibilities"]))
    return "\n".join(parts)


async def _jd_text(job_id: str, job_doc: dict) -> str:
    """Best available JD text for this job (see module docstring for order)."""
    try:
        parsed_col = await get_collection("parsed_jds")
        parsed = await parsed_col.find_one(
            {"sourceJobId": job_id}, sort=[("createdAt", -1)],
        )
        if parsed:
            if parsed.get("requirements"):
                text = _requirements_text(parsed["requirements"])
                if text:
                    return text
            if parsed.get("rawText"):
                return parsed["rawText"]
    except Exception as exc:  # noqa: BLE001 — a missing JD is not an error
        logger.warning("[Brief] parsed_jds lookup failed for %s: %s", job_id, exc)

    return (job_doc.get("jobDetails") or {}).get("description") or ""


async def build_brief(
    pipeline_id: str, job_id: str, hints: Optional[Dict[str, Any]] = None,
) -> SearchBrief:
    """Build the Strategist's input for one pipeline job.

    `hints` are the recruiter's optional brief fields from the form; unknown keys
    are ignored. Raises ValueError("pipeline_not_found" / "job_not_found") so the
    API layer can map them to a 404 the same way the other pipeline calls do.
    """
    pipelines_col = await get_collection("candidatePipelines")
    pipeline = await pipelines_col.find_one({"_id": ObjectId(pipeline_id)})
    if not pipeline:
        raise ValueError("pipeline_not_found")

    job_entry = next(
        (j for j in (pipeline.get("jobs") or []) if j.get("jobId") == job_id), None,
    )
    if not job_entry:
        raise ValueError("job_not_found")

    jobs_col = await get_collection("jobs")
    job_doc = await jobs_col.find_one({"_id": ObjectId(job_id)}) or {}

    # The job doc is authoritative; the pipeline entry is a denormalized copy.
    title = job_doc.get("title") or job_entry.get("jobTitle") or ""
    location = job_doc.get("location") or job_entry.get("jobLocation") or ""

    fields: Dict[str, Any] = {
        "jobTitle": title,
        "jobLocation": location,
        "companyName": pipeline.get("companyName") or "",
        "companyIndustry": pipeline.get("matchedIndustry") or pipeline.get("companyIndustry") or "",
        "jobDescription": await _jd_text(job_id, job_doc),
    }

    # Recruiter hints win over the derived values — they're the human's override.
    for key, value in (hints or {}).items():
        if key in SearchBrief.model_fields and value not in (None, "", []):
            fields[key] = value

    return SearchBrief(**fields)
