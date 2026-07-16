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
import re
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
# Bump whenever the scoring maths changes, so a stored run says which rules made it.
SCORING_VERSION = "match-scoring-4"

# Nominal weights. A component only spends its weight when the JD actually states
# the requirement — see _score_candidate for the renormalisation.
BASE_WEIGHTS: Dict[str, float] = {
    "semantic": 0.50,
    "skillCoverage": 0.30,
    "experience": 0.12,
    "location": 0.08,
}

COMPONENT_LABELS: Dict[str, str] = {
    "semantic": "Semantic similarity",
    "skillCoverage": "Must-have skill coverage",
    "experience": "Years of experience",
    "location": "Location",
}

# A candidate missing the must-haves cannot be a strong match however well the
# prose reads. Coverage buys a ceiling on the final score.
COVERAGE_CEILINGS: List[Tuple[float, float]] = [
    (1.0, 100.0),   # every must-have credited → no cap
    (0.75, 85.0),
    (0.5, 65.0),
    (0.25, 50.0),
    (0.0, 40.0),    # some credit, but under a quarter
]
NO_COVERAGE_CEILING = 25.0  # zero must-haves credited

_FUZZY_MIN_LEN = 4    # below this, fuzzy ratios are noise ("R", "Go", "C#")
_FUZZY_STRONG = 95    # spelling variant of the same thing
_FUZZY_OK = 88        # close enough to credit, but not identical

# German glues words into compounds with no space, so "Lohnsteuer" sits INSIDE
# "Lohnsteuerrecht" as a prefix rather than as a token — and the token-boundary
# rule that killed the "R"-matches-everything bug also blocks that legitimate hit.
# A length floor separates the two: a >=5-char term inside a compound is real
# evidence, a 1-char "R" inside "Arbeitsrecht" is not.
_COMPOUND_MIN_LEN = 5


def _norm_skill(s: str) -> str:
    """Lowercase + strip punctuation that isn't part of a skill name (keeps + # .
    so 'C++', 'C#' and 'React.js' survive)."""
    s = (s or "").lower().strip()
    s = re.sub(r"[^\w\s+#.]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def _tokens_in(needle: str, haystack: str) -> bool:
    """True when `needle` appears in `haystack` on WHOLE-TOKEN boundaries.

    This is the guard the old bare `needle in haystack` lacked: a one-letter skill
    like "R" is a plain substring of "arbeitsrecht", "sap hr3" and most German
    compounds, which handed every candidate 100% must-have coverage on a JD they
    could not do. Requiring token boundaries makes "R" match only a standalone R.
    """
    if not needle or not haystack:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack, flags=re.UNICODE) is not None


def _match_skill(jd_skill: str, cand_skills: List[str]) -> Dict[str, Any]:
    """Credit one must-have skill against a candidate's skill list.

    Returns the evidence for the decision — credit 0..1, which candidate skill
    earned it and by what rule — so a run can explain every point it awarded
    rather than just asserting a coverage percentage.
    """
    jd_n = _norm_skill(jd_skill)
    best: Dict[str, Any] = {
        "skill": jd_skill, "credit": 0.0, "method": "none", "via": None,
        "confidence": 0.0, "note": "No candidate skill matched this requirement.",
    }
    if not jd_n:
        return best

    for cs_raw in cand_skills:
        cs_n = _norm_skill(cs_raw)
        if not cs_n:
            continue

        if cs_n == jd_n:
            return {"skill": jd_skill, "credit": 1.0, "method": "exact", "via": cs_raw,
                    "confidence": 100.0, "note": f"Exact match on “{cs_raw}”."}

        # Every rule is evaluated and the STRONGEST wins. Not first-match-wins: a
        # weaker rule that happens to fire first would otherwise cap the credit —
        # "SAP HR" vs "SAP HR3" is a 92% fuzzy hit (0.75) AND a compound-prefix hit
        # (0.5), and short-circuiting on the compound silently downgraded it.
        cands: List[Dict[str, Any]] = []

        # The candidate names a MORE specific variant of what the JD asked for —
        # JD "SAP" vs candidate "SAP HR3 Payroll". Full credit.
        if _tokens_in(jd_n, cs_n):
            cands.append({"skill": jd_skill, "credit": 1.0, "method": "specific", "via": cs_raw,
                          "confidence": 95.0,
                          "note": f"“{cs_raw}” covers the required “{jd_skill}”."})
        # German compounds: "Entgeltabrechnung" ⊂ "Personalsachbearbeiterin
        # Entgeltabrechnung" needs no space to be real evidence, once the term is
        # long enough that the overlap can't be coincidence.
        elif len(jd_n) >= _COMPOUND_MIN_LEN and jd_n in cs_n:
            cands.append({"skill": jd_skill, "credit": 1.0, "method": "specific", "via": cs_raw,
                          "confidence": 90.0,
                          "note": f"“{cs_raw}” contains the required “{jd_skill}”."})

        # The candidate names a BROADER term than the JD asked for — JD "SAP HR3"
        # vs candidate "SAP", or "Lohnsteuer" for "Lohnsteuerrecht". Related, but
        # not proof of the specific requirement.
        if _tokens_in(cs_n, jd_n) or (len(cs_n) >= _COMPOUND_MIN_LEN and cs_n in jd_n):
            cands.append({"skill": jd_skill, "credit": 0.5, "method": "broader", "via": cs_raw,
                          "confidence": 50.0,
                          "note": f"“{cs_raw}” is part of “{jd_skill}” — half credit, "
                                  f"related but not the whole requirement."})

        # token_sort_ratio, NOT token_set_ratio: the latter returns 100 whenever one
        # token set is a subset of the other, which is exactly the over-crediting
        # the containment rules above already handle deliberately.
        if len(jd_n) >= _FUZZY_MIN_LEN and len(cs_n) >= _FUZZY_MIN_LEN:
            r = float(fuzz.token_sort_ratio(jd_n, cs_n))
            if r >= _FUZZY_STRONG:
                cands.append({"skill": jd_skill, "credit": 1.0, "method": "fuzzy", "via": cs_raw,
                              "confidence": r,
                              "note": f"“{cs_raw}” is a spelling variant ({r:.0f}% similar)."})
            elif r >= _FUZZY_OK:
                cands.append({"skill": jd_skill, "credit": 0.75, "method": "fuzzy", "via": cs_raw,
                              "confidence": r,
                              "note": f"“{cs_raw}” is a close match ({r:.0f}% similar)."})

        for cand in cands:
            if cand["credit"] > best["credit"]:
                best = cand

    return best


def _skill_present(jd_skill: str, cand_skills: List[str]) -> bool:
    """Back-compat boolean view of _match_skill (any credit at all)."""
    return _match_skill(jd_skill, cand_skills)["credit"] > 0


def _skill_evidence_pool(profile: Dict[str, Any]) -> List[str]:
    """Everything the candidate says about themselves that can evidence a skill.

    NOT just `skills`. A German payroll clerk titled "Personalsachbearbeiterin
    Entgeltabrechnung" frequently does not repeat "Entgeltabrechnung" in her skills
    list — the job title already says it. Scoring `skills` alone reported a
    must-have of "Entgeltabrechnung" as MISSING for exactly that person, which is
    the clearest possible false negative: the requirement is her actual job.

    Titles are appended after skills so a real skills-list hit still wins the
    evidence race and gets named as the source.
    """
    pool: List[str] = [s for s in (profile.get("skills") or []) if s]
    if profile.get("currentTitle"):
        pool.append(str(profile["currentTitle"]))
    pool += [str(t) for t in (profile.get("titles") or []) if t]
    pool += [str(e.get("title")) for e in (profile.get("experience") or []) if e.get("title")]
    # Preserve order (evidence priority) while dropping repeats.
    seen: set = set()
    out: List[str] = []
    for item in pool:
        key = _norm_skill(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _coverage_ceiling(coverage: float) -> float:
    if coverage <= 0:
        return NO_COVERAGE_CEILING
    for threshold, ceiling in COVERAGE_CEILINGS:
        if coverage >= threshold:
            return ceiling
    return NO_COVERAGE_CEILING


def _score_candidate(
    jd: Dict[str, Any], profile: Dict[str, Any], sim: float
) -> Tuple[float, Dict[str, float], List[str], Dict[str, Any]]:
    """Blend semantic similarity with deterministic constraints.

    Returns (score 0..100, subscores, missing-must-have-skills, breakdown).

    Two rules make the number mean something:
      * A component the JD never stated (no location, no must-haves, no minimum
        years) is NOT scored as a free 100 — it is dropped and its weight is
        redistributed over the components that actually apply. Otherwise a vague
        JD hands every candidate the same padding and only semantic similarity
        discriminates.
      * Must-have coverage caps the final score. Prose can't buy a candidate past
        a ceiling their missing must-haves set.

    `breakdown` records every input, weight, awarded point and lost point, plus
    per-skill evidence — it is what the UI renders as "why this score".
    """
    cand_skills = _skill_evidence_pool(profile)
    must = [s for s in (jd.get("mustHaveSkills") or []) if s]

    # ── semantic (always applicable) ──
    sim_norm = max(0.0, min(1.0, (sim + 1) / 2)) if sim < 0 else max(0.0, min(1.0, sim))

    # ── must-have coverage ──
    skill_evidence = [_match_skill(s, cand_skills) for s in must]
    if must:
        coverage = sum(e["credit"] for e in skill_evidence) / len(must)
        # A GAP is an absence — nothing in the profile evidences it. A partial hit
        # ("SAP HR" for "SAP HR3") is NOT a gap: calling it one contradicts the very
        # breakdown that credited it, and the reasoning LLM is handed this list as
        # `missingMustHave`, so it writes "Missing SAP HR3" over the top of evidence
        # saying 92% match. Partial credit is reported as partial, everywhere.
        gaps = [e["skill"] for e in skill_evidence if e["credit"] <= 0]
        partial = [e for e in skill_evidence if 0 < e["credit"] < 1.0]
    else:
        coverage = 1.0
        gaps = []
        partial = []

    # ── experience ──
    min_years = jd.get("minYears")
    cand_years = profile.get("totalYears")
    if not min_years:
        years_score = 1.0
        years_note = "The job description states no minimum years — not scored."
    elif cand_years is None:
        years_score = 0.5
        years_note = f"{min_years}+ years required; the CV states no total — scored at half."
    elif cand_years >= min_years:
        years_score = 1.0
        years_note = f"{cand_years} years vs {min_years} required — met."
    else:
        years_score = max(0.0, min(1.0, cand_years / min_years))
        years_note = f"{cand_years} years vs {min_years} required — short by {min_years - cand_years}."

    # ── location ──
    jd_loc = (jd.get("location") or "").lower().strip()
    cand_loc = (profile.get("location") or "").lower().strip()
    if not jd_loc:
        loc_score = 1.0
        loc_note = "The job description names no location — not scored."
    elif not cand_loc:
        loc_score = 0.6
        loc_note = f"Job is in {jd.get('location')}; the CV states no location."
    elif jd_loc in cand_loc or cand_loc in jd_loc or fuzz.partial_ratio(jd_loc, cand_loc) >= 80:
        loc_score = 1.0
        loc_note = f"{profile.get('location')} matches {jd.get('location')}."
    else:
        loc_score = 0.3
        loc_note = f"{profile.get('location')} is away from {jd.get('location')}."

    values = {"semantic": sim_norm, "skillCoverage": coverage,
              "experience": years_score, "location": loc_score}
    applicable = {
        "semantic": True,
        "skillCoverage": bool(must),
        "experience": bool(min_years),
        "location": bool(jd_loc),
    }

    # Redistribute the weight of every component the JD didn't state.
    live = sum(BASE_WEIGHTS[k] for k, on in applicable.items() if on)
    weights = {k: (BASE_WEIGHTS[k] / live if applicable[k] else 0.0) for k in BASE_WEIGHTS}

    base = sum(weights[k] * values[k] for k in BASE_WEIGHTS) * 100
    ceiling = _coverage_ceiling(coverage) if must else 100.0
    score = round(min(base, ceiling), 1)

    notes = {
        "semantic": (f"Cosine similarity {sim:.3f} between the job-description embedding and this "
                     f"CV's embedding. This is the only component that reads the whole document."),
        "skillCoverage": (
            f"{sum(e['credit'] for e in skill_evidence):g} of {len(must)} must-have skill(s) credited."
            if must else "The job description lists no must-have skills — not scored."
        ),
        "experience": years_note,
        "location": loc_note,
    }

    components = []
    for k in BASE_WEIGHTS:
        max_points = round(weights[k] * 100, 1)
        points = round(weights[k] * values[k] * 100, 1)
        comp: Dict[str, Any] = {
            "key": k,
            "label": COMPONENT_LABELS[k],
            "applicable": applicable[k],
            "value": round(values[k] * 100, 1),
            "baseWeight": BASE_WEIGHTS[k],
            "weight": round(weights[k], 4),
            "points": points,
            "maxPoints": max_points,
            "lost": round(max_points - points, 1),
            "note": notes[k],
        }
        if not applicable[k]:
            comp["note"] = notes[k] + (
                f" Its {BASE_WEIGHTS[k]:.0%} weight was redistributed over the components that do apply."
            )
        if k == "skillCoverage":
            comp["skills"] = skill_evidence
        components.append(comp)

    breakdown = {
        "version": SCORING_VERSION,
        "total": score,
        "base": round(base, 1),
        "ceiling": ceiling,
        # Must-haves split by what the evidence actually says, so no consumer has
        # to re-derive it (and get it wrong).
        "missingMustHave": gaps,
        "partialMustHave": [
            {"skill": e["skill"], "credit": e["credit"], "via": e["via"],
             "method": e["method"], "note": e["note"]}
            for e in partial
        ],
        "cappedBy": (
            f"Must-have coverage is {coverage:.0%}, which caps this candidate at {ceiling:g}."
            if score < round(base, 1) else None
        ),
        "similarity": round(sim, 4),
        "components": components,
        "formula": " + ".join(
            f"{weights[k]:.3f}×{values[k] * 100:.1f}" for k in BASE_WEIGHTS if applicable[k]
        ) + f" = {base:.1f}",
    }

    subscores = {
        "semantic": round(sim_norm * 100, 1),
        "skillCoverage": round(coverage * 100, 1),
        "experience": round(years_score * 100, 1),
        "location": round(loc_score * 100, 1),
    }
    return score, subscores, gaps, breakdown


# ── The match run (button) ───────────────────────────────────────────────────
async def run_match(
    db,
    *,
    jd_text: Optional[str] = None,
    jd_bytes: Optional[bytes] = None,
    jd_filename: Optional[str] = None,
    return_top: Optional[int] = None,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse a JD, match against the ingested CV corpus, return top-N with reasons.

    `job_id` links the JD's role spec to a pipeline job, so a JD matched here also
    becomes the requirement that drives that job's sourcing.
    """
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

    # 2/3. JD → the canonical role spec (structured requirements + embedding),
    # parsed once and shared with sourcing rather than re-derived per run.
    from app.services import role_spec_service

    spec = await role_spec_service.get_or_create_for_text(
        db, jd_markdown, job_id=job_id, jd_filename=jd_filename
    )
    requirements = spec["requirements"]
    jd_vector = spec["embedding"]["vector"]
    jd_id = spec["_id"]

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
        score, subscores, gaps, breakdown = _score_candidate(
            requirements, profile, sim_by_id.get(cid, 0.0)
        )
        scored.append({"doc": doc, "cid": cid, "score": score, "subscores": subscores,
                       "gaps": gaps, "breakdown": breakdown})
    # Tie-break on the UNCAPPED base: when several candidates are pinned to the
    # same must-have ceiling their displayed scores are legitimately equal, but the
    # ordering underneath still carries signal.
    scored.sort(key=lambda x: (x["score"], x["breakdown"]["base"]), reverse=True)

    # 6. LLM reasoning for the top N (then keep return_top)
    reason_n = min(settings.MATCH_REASON_TOP_N, len(scored))
    top = scored[:reason_n]
    anonymized = [{
        "id": s["cid"],
        "currentTitle": (s["doc"].get("profile") or {}).get("currentTitle"),
        "totalYears": (s["doc"].get("profile") or {}).get("totalYears"),
        "skills": (s["doc"].get("profile") or {}).get("skills") or [],
        "missingMustHave": s["gaps"],
        # Without this the model sees a must-have absent from `skills` and calls it
        # missing, even when the scorer credited it from the title or a variant.
        "partialMustHave": s["breakdown"]["partialMustHave"],
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

    # 7. assemble a full entry for EVERY scored candidate — not just the top N.
    # The recruiter can only judge whether the ranking is right by seeing what it
    # rejected and why, so the whole ranked list is persisted with its breakdown.
    def _entry(s: Dict[str, Any]) -> Dict[str, Any]:
        doc = s["doc"]
        profile = doc.get("profile") or {}
        contact = doc.get("contact") or {}
        rid = reasons_by_id.get(s["cid"], {})
        return {
            "candidateId": s["cid"],
            "source": "cv",
            "fullName": profile.get("fullName"),
            "currentTitle": profile.get("currentTitle"),
            "location": profile.get("location"),
            "sourceFileName": doc.get("sourceFileName"),
            "score": s["score"],
            "subscores": s["subscores"],
            "breakdown": s["breakdown"],
            "reasons": rid.get("reasons") or _fallback_reasons(requirements, profile, s),
            "reasoning": "llm" if rid.get("reasons") else "deterministic",
            # Deterministic, NOT the model's prose. The scorer knows exactly which
            # must-haves nothing evidences; letting the LLM's wording override it is
            # how "Missing SAP HR3" ended up next to evidence crediting SAP HR at
            # 92%. The model narrates in `reasons`; it does not get to restate facts.
            "gaps": s["gaps"],
            "partial": s["breakdown"]["partialMustHave"],
            "contact": {
                "email": contact.get("email"),
                "phone": contact.get("phone"),
                "linkedin": contact.get("linkedin"),
            },
        }

    all_entries = [_entry(s) for s in scored]
    results = all_entries[:return_top]

    # 8. persist match_run (audit)
    runs_col = db["match_runs"]
    run_doc = {
        "source": "cv",
        "jdId": jd_id,
        "jdTitle": requirements.get("title"),
        "jdText": jd_markdown,
        "jdFileName": jd_filename,
        # Persisted so a saved run can render its must-have chips and explain its
        # own scoring — previously this only existed in the POST response.
        "requirements": requirements,
        "params": {
            "retrieveK": settings.MATCH_RETRIEVE_K,
            "reasonTopN": settings.MATCH_REASON_TOP_N,
            "returnTop": return_top,
        },
        "candidatesConsidered": len(scored),
        "results": results,
        "analysis": {
            "scoringVersion": SCORING_VERSION,
            "baseWeights": BASE_WEIGHTS,
            "reasonedTopN": reason_n,
            "candidates": all_entries,
        },
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
        # From coverage, not len(gaps): `gaps` counts only the wholly-absent, so a
        # partial hit would otherwise be reported as a full match here.
        bd = scored.get("breakdown") or {}
        cov = next((c["value"] / 100 for c in bd.get("components", [])
                    if c["key"] == "skillCoverage"), None)
        if cov is None:  # callers that pass only {"gaps": [...]}
            cov = (len(must) - len(scored.get("gaps") or [])) / len(must)
        matched = cov * len(must)
        shown = f"{matched:.4g}"
        reasons.append(f"Matches {shown}/{len(must)} must-have skills")
    yrs = profile.get("totalYears")
    if yrs is not None:
        reasons.append(f"~{yrs} years of experience")
    if profile.get("currentTitle"):
        reasons.append(f"Current role: {profile['currentTitle']}")
    return reasons or ["Strong semantic match to the role"]
