"""The Role Spec — ONE canonical structured requirement per job.

Why this exists
---------------
The requirement used to be computed at the END of the funnel and thrown away.
Sourcing and matching each parsed the same JD independently, neither told the
other what it concluded, and `parsed_jds.sourceJobId` — the link `brief.py` reads
to reuse the extraction — was never written by anybody. The result: the Search
Strategist aimed with nothing but a job title, we paid to deep-enrich whoever it
found, and only then did the matcher discover that none of them carried a single
must-have skill.

This module makes the requirement the spine instead of the epilogue. Everything
that needs to know what a job wants — the Strategist's search brief, the CV
matcher, the pipeline matcher — resolves it through here and gets the same answer.

Guarantees
----------
  * ONE LLM extraction + ONE embedding per distinct JD text. Both are cached on
    the `parsed_jds` doc and keyed by a content hash, so re-running a match, or
    opening the discovery form for the same job twice, re-uses the parse.
  * `sourceJobId` is actually written, which is what lets a spec created at
    SOURCING time be reused at MATCH time (and vice versa).
  * A JD edit changes the hash, so a stale spec is never silently reused.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.config import settings
from app.services import embedding_service as embeddings
from app.services import llm_extraction_service as llm

logger = logging.getLogger(__name__)

COLLECTION = "parsed_jds"


def _now() -> datetime:
    return datetime.utcnow()


def _text_hash(text: str) -> str:
    """Identity of a JD's CONTENT — the cache key for its parse + embedding."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def resolve_jd_text(job: Dict[str, Any]) -> str:
    """The JD text for a pipeline job.

    Prefers the scraped description. When jobspy captured none (common —
    descriptions are optional), synthesises a minimal JD from the title, any
    structured requirements, and the location, so sourcing and matching still have
    something to work with instead of hard-failing.
    """
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


async def get_or_create_for_text(
    db,
    jd_text: str,
    *,
    job_id: Optional[str] = None,
    jd_filename: Optional[str] = None,
) -> Dict[str, Any]:
    """The role spec for this JD text — parsing and embedding it only if new.

    Returns the full `parsed_jds` doc (`_id` stringified) with `requirements` and
    `embedding.vector` populated. Raises ValueError on empty text.
    """
    jd_text = (jd_text or "").strip()
    if not jd_text:
        raise ValueError("empty job description")

    col = db[COLLECTION]
    digest = _text_hash(jd_text)

    existing = await col.find_one({"textHash": digest})
    if existing and existing.get("requirements") and (existing.get("embedding") or {}).get("vector"):
        # Late-binding the job link: a spec first created by a CV run (which has no
        # job) becomes the job's spec the moment that job asks for the same JD.
        patch: Dict[str, Any] = {}
        if job_id and not existing.get("sourceJobId"):
            patch["sourceJobId"] = job_id
        if jd_filename and not existing.get("sourceFileName"):
            patch["sourceFileName"] = jd_filename
        if patch:
            await col.update_one({"_id": existing["_id"]}, {"$set": {**patch, "updatedAt": _now()}})
            existing.update(patch)
        existing["_id"] = str(existing["_id"])
        logger.info("[RoleSpec] reusing spec %s (job=%s)", existing["_id"], job_id)
        return existing

    requirements = await llm.parse_jd(jd_text)
    vector = await embeddings.embed_text(jd_text[:8000])

    doc = {
        "sourceFileName": jd_filename,
        "sourceJobId": job_id,
        "textHash": digest,
        "rawText": jd_text,
        "requirements": requirements,
        "embedding": {
            "model": settings.EMBEDDING_MODEL,
            "dim": settings.EMBEDDING_DIM,
            "version": embeddings.embedding_version(),
            "vector": vector,
        },
        "extractVersion": llm.extraction_version(),
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    doc["_id"] = str((await col.insert_one(doc)).inserted_id)
    logger.info("[RoleSpec] created spec %s (job=%s, %d must-have(s))",
                doc["_id"], job_id, len(requirements.get("mustHaveSkills") or []))
    return doc


async def get_or_create_for_job(db, job_id: str) -> Optional[Dict[str, Any]]:
    """The role spec for a pipeline job, parsing its JD on first ask.

    Returns None when the job doesn't exist or carries no text worth parsing —
    callers decide whether that's fatal (matching) or merely thin (sourcing).
    """
    try:
        job = await db["jobs"].find_one({"_id": ObjectId(job_id)})
    except Exception:  # noqa: BLE001 — a malformed id is a missing job here
        job = None
    if not job:
        return None

    jd_text = resolve_jd_text(job)
    if not jd_text:
        return None
    return await get_or_create_for_text(db, jd_text, job_id=job_id)


async def find_for_job(db, job_id: str) -> Optional[Dict[str, Any]]:
    """The job's spec if one already exists — never parses. For read paths that
    must not incur LLM cost."""
    doc = await db[COLLECTION].find_one({"sourceJobId": job_id}, sort=[("createdAt", -1)])
    if doc:
        doc["_id"] = str(doc["_id"])
    return doc
