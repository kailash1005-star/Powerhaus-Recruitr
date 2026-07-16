"""Assemble a SearchBrief from what the database already knows about a job.

The recruiter should never retype what we have. This gathers the job title,
location, company context, the JD, AND the structured requirements the matching
engine will later score against — then layers the recruiter's optional hints on
top.

That last part is the point. The must-have skills are what the job actually
needs, so they have to shape the SEARCH, not just grade the results afterwards.
The Strategist is explicitly written to fold skill signal into the job titles it
proposes ("must-have S/4HANA → add 'SAP S/4HANA Consultant' as a title"); it can
only do that if we hand it the skills. Previously `mustHaveSkills`, `minYears`
and `seniorityHint` were left empty unless a recruiter typed them by hand, and
the requirement only entered the funnel at match time — after the Apify spend.

Requirements come from the job's role spec (`role_spec_service`), the same object
the matcher scores with, so the search and the scorecard can never disagree.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from bson import ObjectId

from app.database import get_collection, get_database
from app.services.sourcing.models import SearchBrief

logger = logging.getLogger(__name__)


async def _role_spec(job_id: str, job_doc: dict) -> Dict[str, Any]:
    """The job's role spec, parsing the JD on first ask.

    Never raises: sourcing must still work on a job whose JD can't be parsed (or
    with no LLM key configured) — it just falls back to a thinner brief.
    """
    try:
        from app.services import role_spec_service

        db = await get_database()
        spec = await role_spec_service.get_or_create_for_job(db, job_id)
        if spec:
            return spec
    except Exception as exc:  # noqa: BLE001 — a missing/unparseable JD is not an error
        logger.warning("[Brief] role spec unavailable for %s: %s", job_id, exc)
    return {}


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

    spec = await _role_spec(job_id, job_doc)
    req: Dict[str, Any] = spec.get("requirements") or {}

    fields: Dict[str, Any] = {
        "jobTitle": title,
        "jobLocation": location,
        "companyName": pipeline.get("companyName") or "",
        "companyIndustry": pipeline.get("matchedIndustry") or pipeline.get("companyIndustry") or "",
        # The RAW JD, not a re-rendering of the extraction: it is the strongest
        # signal the Strategist has for the vocabulary and language real profiles
        # use. The structured fields below carry the extraction separately.
        "jobDescription": spec.get("rawText")
        or (job_doc.get("jobDetails") or {}).get("description")
        or "",
    }

    # What the matcher will grade on — now also what the search aims at.
    if req.get("mustHaveSkills"):
        fields["mustHaveSkills"] = req["mustHaveSkills"]
    if req.get("niceToHaveSkills"):
        fields["niceToHaveSkills"] = req["niceToHaveSkills"]
    if req.get("minYears") is not None:
        fields["minYears"] = req["minYears"]
    if req.get("seniority"):
        fields["seniorityHint"] = req["seniority"]
    # The JD's own location is more precise than the posting's when both exist.
    if req.get("location") and not location:
        fields["jobLocation"] = req["location"]

    # Recruiter hints win over the derived values — they're the human's override.
    for key, value in (hints or {}).items():
        if key in SearchBrief.model_fields and value not in (None, "", []):
            fields[key] = value

    brief = SearchBrief(**fields)
    logger.info(
        "[Brief] %s/%s — %d must-have(s), %d nice-to-have(s), minYears=%s, seniority=%r",
        pipeline_id, job_id, len(brief.mustHaveSkills), len(brief.niceToHaveSkills),
        brief.minYears, brief.seniorityHint,
    )
    return brief
