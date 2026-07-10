"""
Matching Service — the orchestrator for the CV ↔ JD engine.

Two public flows:
  * ingest_cv(...)  — one CV: hash/dedup → Docling → LLM fields → embed → store
                      (+ vector-store upsert). Robust per-file; never raises to
                      the batch.
  * run_match(...)  — the "Run Matching" button: JD → Docling → requirements →
                      embed → vector retrieve → deterministic score → LLM
                      reasoning → TOP N with reasons + contact, persisted.

Determinism: hard constraints (must-have skills, min years, location) are scored
in code; the LLM only explains. Every match_run records model versions.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bson import Binary, ObjectId
from rapidfuzz import fuzz

from app.config import settings
from app.services import document_parsing_service as docparser
from app.services import embedding_service as embeddings
from app.services import llm_extraction_service as llm
from app.services.vector_store import get_vector_store

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.utcnow()


_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "txt": "text/plain",
    "html": "text/html",
    "htm": "text/html",
    "rtf": "application/rtf",
}


def content_type_for(filename: Optional[str]) -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


def _embed_text_from_profile(profile: Dict[str, Any], markdown: str) -> str:
    """Compose the text we embed — professional content only (PII excluded).
    Falls back to the raw markdown if extraction was too sparse."""
    parts: List[str] = []
    if profile.get("currentTitle"):
        parts.append(str(profile["currentTitle"]))
    parts += [str(t) for t in (profile.get("titles") or [])]
    parts += [str(s) for s in (profile.get("skills") or [])]
    for exp in (profile.get("experience") or []):
        seg = " ".join(filter(None, [exp.get("title"), exp.get("summary")]))
        if seg:
            parts.append(seg)
    parts += [str(e) for e in (profile.get("education") or [])]
    parts += [str(c) for c in (profile.get("certifications") or [])]
    composed = ". ".join(p for p in parts if p).strip()
    if len(composed) < 40:  # extraction too thin — use the clean document text
        composed = (markdown or "")[:8000]
    return composed[:8000]


# ── CV ingestion ─────────────────────────────────────────────────────────────
async def ingest_cv(db, data: bytes, filename: Optional[str], batch_id: Optional[str]) -> Dict[str, Any]:
    """Ingest a single CV. Returns a status dict; does not raise."""
    col = db["cv_candidates"]
    content_hash = hashlib.sha256(data).hexdigest()

    existing = await col.find_one({"contentHash": content_hash}, {"_id": 1, "status": 1})
    if existing:
        # A previously-embedded CV is a true duplicate — skip. But a FAILED one
        # (e.g. parsed before Docling was available) should be retried, not
        # silently skipped, so re-uploading the same file recovers it.
        if existing.get("status") != "failed":
            return {"file": filename, "status": "duplicate", "id": str(existing["_id"])}
        cid = existing["_id"]
        await col.update_one(
            {"_id": cid},
            {"$set": {"status": "pending", "error": None, "batchId": batch_id, "updatedAt": _now()}},
        )
    else:
        doc = {
            "contentHash": content_hash,
            "sourceFileName": filename,
            "batchId": batch_id,
            "status": "pending",
            "createdAt": _now(),
            "updatedAt": _now(),
        }
        cid = (await col.insert_one(doc)).inserted_id

    # Keep the ORIGINAL file bytes so the recruiter can download the real CV
    # later (top candidates in a saved run). Stored in a side collection to keep
    # cv_candidates lean. Best-effort — never fail ingest over this.
    try:
        await db["cv_files"].update_one(
            {"_id": cid},
            {"$set": {
                "filename": filename,
                "contentType": content_type_for(filename),
                "size": len(data),
                "data": Binary(data),
                "updatedAt": _now(),
            }},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[Matching] could not store original file for %s: %s", filename, e)

    try:
        markdown = await docparser.parse_bytes(data, filename)
        if not markdown.strip():
            raise ValueError("no text extracted from document")

        fields = await llm.extract_cv_fields(markdown)
        profile = {
            "fullName": fields.get("fullName"),
            "location": fields.get("location"),
            "totalYears": fields.get("totalYears"),
            "currentTitle": fields.get("currentTitle"),
            "skills": fields.get("skills") or [],
            "titles": fields.get("titles") or [],
            "education": fields.get("education") or [],
            "certifications": fields.get("certifications") or [],
            "experience": fields.get("experience") or [],
        }
        contact = {
            "email": fields.get("email"),
            "phone": fields.get("phone"),
            "linkedin": fields.get("linkedin"),
        }

        embed_text = _embed_text_from_profile(profile, markdown)
        vector = await embeddings.embed_text(embed_text)

        await col.update_one(
            {"_id": cid},
            {"$set": {
                "markdown": markdown,
                "profile": profile,
                "contact": contact,
                "embedding": {
                    "model": settings.EMBEDDING_MODEL,
                    "dim": settings.EMBEDDING_DIM,
                    "version": embeddings.embedding_version(),
                    "vector": vector,
                },
                "status": "embedded",
                "error": None,
                "updatedAt": _now(),
            }},
        )

        # External vector store (Pinecone) — Mongo backend is a no-op upsert.
        store = get_vector_store(db)
        await store.upsert([{
            "id": str(cid),
            "vector": vector,
            "metadata": {
                "location": profile.get("location") or "",
                "totalYears": float(profile.get("totalYears") or 0),
            },
        }])

        return {"file": filename, "status": "embedded", "id": str(cid),
                "name": profile.get("fullName")}

    except Exception as e:  # noqa: BLE001 — one bad CV must not fail the batch
        logger.exception("[Matching] CV ingest failed for %s", filename)
        await col.update_one(
            {"_id": cid},
            {"$set": {"status": "failed", "error": str(e)[:500], "updatedAt": _now()}},
        )
        return {"file": filename, "status": "failed", "id": str(cid), "error": str(e)[:200]}


# ── Deterministic scoring ────────────────────────────────────────────────────
def _skill_present(jd_skill: str, cand_skills: List[str], threshold: int = 82) -> bool:
    jd_skill = (jd_skill or "").lower().strip()
    if not jd_skill:
        return False
    for cs in cand_skills:
        cs = (cs or "").lower().strip()
        if not cs:
            continue
        if jd_skill in cs or cs in jd_skill:
            return True
        if fuzz.token_set_ratio(jd_skill, cs) >= threshold:
            return True
    return False


def _score_candidate(jd: Dict[str, Any], profile: Dict[str, Any], sim: float) -> Tuple[float, Dict[str, float], List[str]]:
    """Blend semantic similarity with deterministic constraints. Returns
    (score 0..100, subscores, missing-must-have-skills)."""
    cand_skills = profile.get("skills") or []
    must = [s for s in (jd.get("mustHaveSkills") or []) if s]

    if must:
        matched = [s for s in must if _skill_present(s, cand_skills)]
        coverage = len(matched) / len(must)
        gaps = [s for s in must if s not in matched]
    else:
        coverage = 1.0
        gaps = []

    min_years = jd.get("minYears")
    cand_years = profile.get("totalYears")
    if not min_years:
        years_score = 1.0
    elif cand_years is None:
        years_score = 0.5
    elif cand_years >= min_years:
        years_score = 1.0
    else:
        years_score = max(0.0, min(1.0, cand_years / min_years))

    jd_loc = (jd.get("location") or "").lower().strip()
    cand_loc = (profile.get("location") or "").lower().strip()
    if not jd_loc:
        loc_score = 1.0
    elif not cand_loc:
        loc_score = 0.6
    elif jd_loc in cand_loc or cand_loc in jd_loc or fuzz.partial_ratio(jd_loc, cand_loc) >= 80:
        loc_score = 1.0
    else:
        loc_score = 0.3

    sim_norm = max(0.0, min(1.0, (sim + 1) / 2)) if sim < 0 else max(0.0, min(1.0, sim))

    blended = 0.50 * sim_norm + 0.30 * coverage + 0.12 * years_score + 0.08 * loc_score
    score = round(blended * 100, 1)
    subscores = {
        "semantic": round(sim_norm * 100, 1),
        "skillCoverage": round(coverage * 100, 1),
        "experience": round(years_score * 100, 1),
        "location": round(loc_score * 100, 1),
    }
    return score, subscores, gaps


# ── The match run (button) ───────────────────────────────────────────────────
async def run_match(
    db,
    *,
    jd_text: Optional[str] = None,
    jd_bytes: Optional[bytes] = None,
    jd_filename: Optional[str] = None,
    return_top: Optional[int] = None,
) -> Dict[str, Any]:
    """Parse a JD, match against the ingested CV corpus, return top-N with reasons."""
    return_top = return_top or settings.MATCH_RETURN_TOP

    # 1. JD → markdown
    if jd_bytes:
        jd_markdown = await docparser.parse_bytes(jd_bytes, jd_filename)
    elif jd_text:
        jd_markdown = jd_text.strip()
    else:
        raise ValueError("provide jd_text or jd_bytes")
    if not jd_markdown.strip():
        raise ValueError("empty job description")

    # 2. JD → structured requirements + embedding
    requirements = await llm.parse_jd(jd_markdown)
    jd_vector = await embeddings.embed_text(jd_markdown[:8000])

    # 3. persist parsed_jds
    jds_col = db["parsed_jds"]
    jd_doc = {
        "sourceFileName": jd_filename,
        "rawText": jd_markdown,
        "requirements": requirements,
        "embedding": {
            "model": settings.EMBEDDING_MODEL,
            "dim": settings.EMBEDDING_DIM,
            "version": embeddings.embedding_version(),
            "vector": jd_vector,
        },
        "createdAt": _now(),
    }
    jd_id = str((await jds_col.insert_one(jd_doc)).inserted_id)

    # 4. retrieve candidates by similarity
    store = get_vector_store(db)
    hits = await store.query(jd_vector, top_k=settings.MATCH_RETRIEVE_K)
    sim_by_id = {cid: score for cid, score in hits}

    cv_col = db["cv_candidates"]
    candidates: List[Dict[str, Any]] = []
    if sim_by_id:
        obj_ids = [ObjectId(cid) for cid in sim_by_id.keys()]
        async for doc in cv_col.find({"_id": {"$in": obj_ids}}):
            candidates.append(doc)

    # 5. deterministic score
    scored: List[Dict[str, Any]] = []
    for doc in candidates:
        cid = str(doc["_id"])
        profile = doc.get("profile") or {}
        score, subscores, gaps = _score_candidate(requirements, profile, sim_by_id.get(cid, 0.0))
        scored.append({"doc": doc, "cid": cid, "score": score, "subscores": subscores, "gaps": gaps})
    scored.sort(key=lambda x: x["score"], reverse=True)

    # 6. LLM reasoning for the top N (then keep return_top)
    reason_n = min(settings.MATCH_REASON_TOP_N, len(scored))
    top = scored[:reason_n]
    anonymized = [{
        "id": s["cid"],
        "currentTitle": (s["doc"].get("profile") or {}).get("currentTitle"),
        "totalYears": (s["doc"].get("profile") or {}).get("totalYears"),
        "skills": (s["doc"].get("profile") or {}).get("skills") or [],
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
            logger.warning("[Matching] reasoning step failed; using deterministic reasons")

    # 7. assemble final top results
    results: List[Dict[str, Any]] = []
    for s in top[:return_top]:
        doc = s["doc"]
        profile = doc.get("profile") or {}
        contact = doc.get("contact") or {}
        rid = reasons_by_id.get(s["cid"], {})
        reasons = rid.get("reasons") or _fallback_reasons(requirements, profile, s)
        results.append({
            "candidateId": s["cid"],
            "fullName": profile.get("fullName"),
            "currentTitle": profile.get("currentTitle"),
            "location": profile.get("location"),
            "score": s["score"],
            "subscores": s["subscores"],
            "reasons": reasons,
            "gaps": rid.get("gaps") or s["gaps"],
            "contact": {
                "email": contact.get("email"),
                "phone": contact.get("phone"),
                "linkedin": contact.get("linkedin"),
            },
        })

    # 8. persist match_run (audit)
    runs_col = db["match_runs"]
    run_doc = {
        "jdId": jd_id,
        "jdTitle": requirements.get("title"),
        "jdText": jd_markdown,
        "jdFileName": jd_filename,
        "params": {
            "retrieveK": settings.MATCH_RETRIEVE_K,
            "reasonTopN": settings.MATCH_REASON_TOP_N,
            "returnTop": return_top,
        },
        "candidatesConsidered": len(scored),
        "results": results,
        "modelVersions": {
            "extract": llm.extraction_version(),
            "embed": embeddings.embedding_version(),
            "reason": llm.reasoning_version(),
        },
        "vectorBackend": settings.VECTOR_BACKEND,
        "createdAt": _now(),
    }
    match_run_id = str((await runs_col.insert_one(run_doc)).inserted_id)

    return {
        "matchRunId": match_run_id,
        "jdId": jd_id,
        "jdTitle": requirements.get("title"),
        "requirements": requirements,
        "candidatesConsidered": len(scored),
        "results": results,
    }


def _fallback_reasons(jd: Dict[str, Any], profile: Dict[str, Any], scored: Dict[str, Any]) -> List[str]:
    """Deterministic reasons if the LLM reasoning step is unavailable."""
    reasons: List[str] = []
    must = jd.get("mustHaveSkills") or []
    if must:
        matched = len(must) - len(scored["gaps"])
        reasons.append(f"Matches {matched}/{len(must)} must-have skills")
    yrs = profile.get("totalYears")
    if yrs is not None:
        reasons.append(f"~{yrs} years of experience")
    if profile.get("currentTitle"):
        reasons.append(f"Current role: {profile['currentTitle']}")
    return reasons or ["Strong semantic match to the role"]
