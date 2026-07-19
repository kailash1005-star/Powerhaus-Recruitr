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
# v5: calibrated semantic component (was raw cosine — compressed into ~0.2-0.5
#     for related JD↔profile pairs and discontinuous at 0), plus the anchored-
#     rubric judge blend (see apply_judge).
SCORING_VERSION = "match-scoring-6"

# Cosine similarity from text-embedding-3-small does not use the 0..1 range the
# weighted blend assumes: unrelated professional texts still land ~0.1, and an
# excellent JD↔profile pair rarely clears ~0.6. Feeding it in raw meant the
# LARGEST-weighted component could barely reach half its points for a perfect
# candidate. This affine calibration maps the observed working range onto 0..1;
# it is monotone and continuous (the old code had a jump at exactly 0), and the
# RAW similarity is still stored on every breakdown for audit.
SIM_CALIBRATION_FLOOR = 0.10   # at/below: reads as unrelated → 0
SIM_CALIBRATION_CEIL = 0.60    # at/above: reads as excellent → 1


def calibrate_similarity(sim: float) -> float:
    """Map a raw cosine similarity onto the 0..1 scale the blend expects."""
    span = SIM_CALIBRATION_CEIL - SIM_CALIBRATION_FLOOR
    return max(0.0, min(1.0, (sim - SIM_CALIBRATION_FLOOR) / span))

# Nominal weights. A component only spends its weight when the JD actually states
# the requirement — see _score_candidate for the renormalisation.
BASE_WEIGHTS: Dict[str, float] = {
    "semantic": 0.50,
    "skillCoverage": 0.30,
    "experience": 0.12,
    "location": 0.08,
}

# Recruiter-facing names. "Semantic similarity" is what the maths is called, not
# what it means to someone reading it beside a candidate's name.
COMPONENT_LABELS: Dict[str, str] = {
    "semantic": "Profile fit",
    "skillCoverage": "Must-have skills",
    "experience": "Experience",
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


def _skill_variants(jd_skill: str) -> List[str]:
    """The names a JD requirement actually goes by in profiles.

    JDs write "Payroll (PY)" — profiles write either word alone ("SAP HCM PA,
    PY, OM"). Treating the parenthesised abbreviation as part of ONE literal
    string meant the scorer demanded both words together and credited neither.
    Each parenthesised group becomes its own variant (split on /,; for
    "(PY/PT)"-style lists), alongside the base with the parens removed.
    """
    raw = (jd_skill or "").strip()
    if not raw:
        return []
    variants: List[str] = [raw]
    stripped = re.sub(r"\([^)]*\)", " ", raw).strip()
    if stripped and _norm_skill(stripped) != _norm_skill(raw):
        variants.append(stripped)
    for group in re.findall(r"\(([^)]{1,60})\)", raw):
        for part in re.split(r"[/,;]", group):
            part = part.strip()
            if part:
                variants.append(part)
    seen: set = set()
    out: List[str] = []
    for v in variants:
        key = _norm_skill(v)
        if key and key not in seen:
            seen.add(key)
            out.append(v)
    return out


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


def _snippet_for(variant_n: str, text: str, width: int = 60) -> str:
    """A short display excerpt of `text` around the first occurrence of the
    normalized variant — tolerant of the hyphens/commas normalization removed."""
    pattern = r"[\s\-_/,.]*".join(re.escape(t) for t in variant_n.split())
    m = re.search(pattern, text, flags=re.IGNORECASE | re.UNICODE)
    if not m:
        return text[: width * 2].strip()
    start = max(0, m.start() - width)
    end = min(len(text), m.end() + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return (prefix + text[start:end].strip() + suffix)


def _match_skill(
    jd_skill: str,
    cand_skills: List[str],
    free_texts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Credit one must-have skill against a candidate's evidence.

    Two evidence tiers, checked strongest-first:
      * `cand_skills` — SHORT items (skills list, titles, headline). Matched with
        the full rule set including fuzzy and the broader/half-credit rule.
      * `free_texts` — free-text entries (profile summary, one per experience
        entry). LinkedIn profiles routinely carry an EMPTY skills array while the
        experience bullet points name every module the person works in
        ("Schwerpunkte SAP HCM PA, PY, OM…"); scoring the short items alone
        reported exactly those people as having no evidence — the costliest
        false negative this scorer can produce. Free text only credits on
        containment of a whole variant (full credit) or co-occurrence of all its
        terms inside ONE entry (partial) — never fuzzy, never cross-entry
        scatter, so a long profile can't buy credit by accident.

    Returns the evidence for the decision — credit 0..1, which evidence earned
    it and by what rule — so a run can explain every point it awarded.
    """
    variants = _skill_variants(jd_skill)
    jd_n = _norm_skill(jd_skill)
    best: Dict[str, Any] = {
        "skill": jd_skill, "credit": 0.0, "method": "none", "via": None,
        "confidence": 0.0, "note": "No candidate skill matched this requirement.",
    }
    if not jd_n or not variants:
        return best

    variant_ns = [_norm_skill(v) for v in variants]

    for cs_raw in cand_skills:
        cs_n = _norm_skill(cs_raw)
        if not cs_n:
            continue

        # Every rule is evaluated and the STRONGEST wins. Not first-match-wins: a
        # weaker rule that happens to fire first would otherwise cap the credit —
        # "SAP HR" vs "SAP HR3" is a 92% fuzzy hit (0.75) AND a compound-prefix hit
        # (0.5), and short-circuiting on the compound silently downgraded it.
        cands: List[Dict[str, Any]] = []

        for v_raw, v_n in zip(variants, variant_ns):
            if cs_n == v_n:
                return {"skill": jd_skill, "credit": 1.0, "method": "exact", "via": cs_raw,
                        "confidence": 100.0, "note": f"Exact match on “{cs_raw}”."}

            # The candidate names a MORE specific variant of what the JD asked
            # for — JD "SAP" vs candidate "SAP HR3 Payroll". Full credit.
            if _tokens_in(v_n, cs_n):
                cands.append({"skill": jd_skill, "credit": 1.0, "method": "specific", "via": cs_raw,
                              "confidence": 95.0,
                              "note": f"“{cs_raw}” covers the required “{jd_skill}”."})
            # German compounds: "Entgeltabrechnung" ⊂ "Personalsachbearbeiterin
            # Entgeltabrechnung" needs no space to be real evidence, once the term
            # is long enough that the overlap can't be coincidence.
            elif len(v_n) >= _COMPOUND_MIN_LEN and v_n in cs_n:
                cands.append({"skill": jd_skill, "credit": 1.0, "method": "specific", "via": cs_raw,
                              "confidence": 90.0,
                              "note": f"“{cs_raw}” contains the required “{jd_skill}”."})
            # All the variant's terms appear in the item, just not adjacently —
            # "SAP-HCM" vs the title "SAP-Spezialistin HCM". Short items only
            # (a title carries a few words; every term present IS the claim).
            elif len(v_n.split()) > 1 and all(_tokens_in(t, cs_n) for t in v_n.split()):
                cands.append({"skill": jd_skill, "credit": 1.0, "method": "all-terms", "via": cs_raw,
                              "confidence": 90.0,
                              "note": f"“{cs_raw}” carries every term of “{jd_skill}”."})

        # The candidate names a BROADER term than the JD asked for — JD "SAP HR3"
        # vs candidate "SAP", or "Lohnsteuer" for "Lohnsteuerrecht". Related, but
        # not proof of the specific requirement. Base form only: an abbreviation
        # variant ("PY") sitting inside an unrelated phrase is not a broader hit.
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

    # ── Free-text tier — only consulted when the short items fell short. ──
    if best["credit"] < 1.0:
        for text in (free_texts or []):
            text_n = _norm_skill(text)
            if not text_n:
                continue
            for v_raw, v_n in zip(variants, variant_ns):
                # Whole variant present in the entry (token-bounded, or embedded
                # in a German compound when long enough) → full credit.
                if _tokens_in(v_n, text_n) or (len(v_n) >= _COMPOUND_MIN_LEN and v_n in text_n):
                    snippet = _snippet_for(v_n, text)
                    best = {"skill": jd_skill, "credit": 1.0, "method": "profile-text",
                            "via": snippet, "confidence": 85.0,
                            "note": f"The profile text evidences “{jd_skill}”: “{snippet}”."}
                    return best
                # All terms of a multi-word variant inside this ONE entry — the
                # words co-occur in the same role description. Partial credit:
                # co-occurrence is strong but not the literal phrase.
                terms = v_n.split()
                if len(terms) > 1 and all(_tokens_in(t, text_n) for t in terms):
                    if 0.75 > best["credit"]:
                        snippet = _snippet_for(terms[0], text)
                        best = {"skill": jd_skill, "credit": 0.75, "method": "profile-text-terms",
                                "via": snippet, "confidence": 70.0,
                                "note": (f"Every term of “{jd_skill}” appears in one experience "
                                         f"entry: “{snippet}”.")}
    return best


def _skill_present(jd_skill: str, cand_skills: List[str]) -> bool:
    """Back-compat boolean view of _match_skill (any credit at all)."""
    return _match_skill(jd_skill, cand_skills)["credit"] > 0


def _skill_evidence_pool(profile: Dict[str, Any]) -> List[str]:
    """Every SHORT self-description that can evidence a skill.

    NOT just `skills`. A German payroll clerk titled "Personalsachbearbeiterin
    Entgeltabrechnung" frequently does not repeat "Entgeltabrechnung" in her skills
    list — the job title already says it. Scoring `skills` alone reported a
    must-have of "Entgeltabrechnung" as MISSING for exactly that person, which is
    the clearest possible false negative: the requirement is her actual job.

    Titles are appended after skills so a real skills-list hit still wins the
    evidence race and gets named as the source. Free-text evidence (summaries)
    lives in _free_text_entries — different matching rules apply there.
    """
    pool: List[str] = [s for s in (profile.get("skills") or []) if s]
    if profile.get("currentTitle"):
        pool.append(str(profile["currentTitle"]))
    if profile.get("headline"):
        pool.append(str(profile["headline"]))
    pool += [str(t) for t in (profile.get("titles") or []) if t]
    pool += [str(e.get("title")) for e in (profile.get("experience") or []) if e.get("title")]
    pool += [str(c) for c in (profile.get("certifications") or []) if c]
    # Preserve order (evidence priority) while dropping repeats.
    seen: set = set()
    out: List[str] = []
    for item in pool:
        key = _norm_skill(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _free_text_entries(profile: Dict[str, Any]) -> List[str]:
    """Free-text evidence, one entry per coherent block.

    Each experience entry is its own block (title + summary together, so the
    words of one role's description can co-occur) rather than one concatenated
    corpus — the co-occurrence rule in _match_skill must never credit terms
    scattered across UNRELATED roles.
    """
    entries: List[str] = []
    for key in ("summary", "about"):
        v = (profile.get(key) or "").strip()
        if v:
            entries.append(v[:2000])
    for e in (profile.get("experience") or [])[:12]:
        e = e or {}
        block = " — ".join(filter(None, [str(e.get("title") or ""), str(e.get("summary") or "")])).strip(" —")
        if block:
            entries.append(block[:2000])
    for e in (profile.get("education") or [])[:6]:
        s = str(e or "").strip()
        if s and s.lower() not in ("none", "{}"):
            entries.append(s[:400])
    return entries


def _coverage_ceiling(coverage: float) -> float:
    if coverage <= 0:
        return NO_COVERAGE_CEILING
    for threshold, ceiling in COVERAGE_CEILINGS:
        if coverage >= threshold:
            return ceiling
    return NO_COVERAGE_CEILING


def _score_candidate(
    jd: Dict[str, Any], profile: Dict[str, Any], sim: float,
    *, forced_credits: Optional[Dict[str, Dict[str, Any]]] = None,
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

    `forced_credits` is the QA auditor's channel: {skill: {"quote": …}} entries
    whose quotes were MECHANICALLY verified against the profile text override a
    lower deterministic credit. The override flows through the normal coverage →
    ceiling → score math and is labelled `qa_verified` in the evidence, so a
    corrected score is exactly as auditable as an uncorrected one. It can only
    RAISE a skill's credit — QA never argues a candidate downward (see
    match_qa_service for why that asymmetry is the whole point).

    `breakdown` records every input, weight, awarded point and lost point, plus
    per-skill evidence — it is what the UI renders as "why this score".
    """
    cand_skills = _skill_evidence_pool(profile)
    free_texts = _free_text_entries(profile)
    must = [s for s in (jd.get("mustHaveSkills") or []) if s]

    # ── semantic (always applicable) ──
    sim_norm = calibrate_similarity(sim)

    # ── must-have coverage ──
    skill_evidence = [_match_skill(s, cand_skills, free_texts) for s in must]
    for i, e in enumerate(skill_evidence):
        forced = (forced_credits or {}).get(e["skill"])
        if forced and e["credit"] < 1.0:
            quote = str(forced.get("quote") or "")[:160]
            skill_evidence[i] = {
                "skill": e["skill"], "credit": 1.0, "method": "qa_verified",
                "via": quote, "confidence": 80.0,
                "note": f"QA auditor verified evidence in the profile: “{quote}”.",
            }
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
        # Recruiter-facing copy. The raw similarity stays on the breakdown as
        # `similarity` for auditing, but "cosine"/"embedding" mean nothing to the
        # person reading this next to a candidate's name.
        "semantic": ("How closely this whole profile reads against the whole role — the only "
                     "part of the score that weighs the entire document rather than a checklist."),
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
        # The calibrated value that actually entered the blend, plus the mapping
        # that produced it — a stored run must be able to explain its own maths.
        "similarityCalibrated": round(sim_norm, 4),
        "similarityCalibration": {"floor": SIM_CALIBRATION_FLOOR, "ceil": SIM_CALIBRATION_CEIL},
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


def apply_judge(scored: Dict[str, Any], judge_item: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Blend the anchored-rubric judge verdict into a scored candidate, in place.

    Final = (1-w)·deterministic + w·judgeFit, then re-capped by the must-have
    coverage ceiling: the judge can pull a score down or up, but prose can never
    lift a candidate past what their evidenced must-haves allow. Every input to
    the blend is recorded on the breakdown so a stored run can show exactly why
    the number moved — which candidate the judge disagreed on, by how much, and
    against which rubric.

    `judge_item` is a validated JudgeItem dict (or None when the judge call was
    unavailable — the deterministic score then stands, visibly unblended).
    """
    bd = scored.get("breakdown") or {}
    if not judge_item:
        bd["judge"] = None
        return scored
    w = max(0.0, min(1.0, float(settings.MATCH_JUDGE_WEIGHT)))
    det = float(scored["score"])
    fit = float(judge_item.get("fitScore") or 0.0)
    ceiling = float(bd.get("ceiling") or 100.0)
    uncapped = (1 - w) * det + w * fit
    blended = round(min(uncapped, ceiling), 1)
    bd["judge"] = {
        "fitScore": round(fit, 1),
        "verdict": judge_item.get("verdict") or "",
        "weight": w,
        "deterministicScore": det,
        "blended": blended,
        "cappedByCeiling": blended < round(uncapped, 1),
        "rubric": "fit-rubric-1",
    }
    bd["total"] = blended
    scored["score"] = blended
    return scored


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

    The whole run is metered under the "matching" cost stage — this engine's
    embedding/extraction/judge spend used to land as orphan events with no
    attribution while only the pipeline engine metered properly.
    """
    from app.services import cost_service

    async with cost_service.cost_context(
        cost_service.STAGE_MATCHING, label=(jd_filename or "CV corpus match"), jobId=job_id,
    ):
        return await _run_match_impl(
            db, jd_text=jd_text, jd_bytes=jd_bytes, jd_filename=jd_filename,
            return_top=return_top, job_id=job_id,
        )


async def _run_match_impl(
    db,
    *,
    jd_text: Optional[str] = None,
    jd_bytes: Optional[bytes] = None,
    jd_filename: Optional[str] = None,
    return_top: Optional[int] = None,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
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

    # A rich JD that parsed to zero must-haves means the checklist components all
    # drop out and "Profile fit" carries the whole score. That is a legitimate
    # outcome for a two-line brief, but for a real JD it usually means the parse
    # missed — say so on the run instead of silently shipping a similarity-only
    # ranking that looks like a normal one.
    requirements_warning: Optional[str] = None
    if not (requirements.get("mustHaveSkills") or []) and len(jd_markdown) > 400:
        requirements_warning = (
            "No must-have skills were extracted from this job description, so the "
            "score is driven by overall profile fit (plus experience/location where "
            "stated) with no skills checklist. Review the JD text or the parsed "
            "requirements before trusting this ranking."
        )
        logger.warning("[Matching] %s (jd=%s)", requirements_warning, jd_id)

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

    # 5. deterministic score — per-candidate isolated. One unreadable profile
    # used to 500 the whole run; now it is recorded on the run and skipped.
    scored: List[Dict[str, Any]] = []
    scoring_errors: List[Dict[str, Any]] = []
    for doc in candidates:
        cid = str(doc["_id"])
        try:
            profile = doc.get("profile") or {}
            score, subscores, gaps, breakdown = _score_candidate(
                requirements, profile, sim_by_id.get(cid, 0.0)
            )
            scored.append({"doc": doc, "cid": cid, "score": score, "subscores": subscores,
                           "gaps": gaps, "breakdown": breakdown})
        except Exception as e:  # noqa: BLE001 — isolate, record, continue
            logger.exception("[Matching] scoring failed for candidate %s", cid)
            scoring_errors.append({"candidateId": cid, "error": str(e)[:200]})
    # Tie-break on the UNCAPPED base: when several candidates are pinned to the
    # same must-have ceiling their displayed scores are legitimately equal, but the
    # ordering underneath still carries signal.
    scored.sort(key=lambda x: (x["score"], x["breakdown"]["base"]), reverse=True)

    # 6. Anchored-rubric judge for the top N. One batched call; the verdicts are
    # blended into the deterministic score (apply_judge), then the list is
    # re-ranked. Best-effort: a lost judge call leaves deterministic scores,
    # visibly marked `reasoning: "deterministic"` — never a hidden default.
    reason_n = min(settings.MATCH_REASON_TOP_N, len(scored))
    top = scored[:reason_n]

    def _judge_view(s: Dict[str, Any]) -> Dict[str, Any]:
        p = s["doc"].get("profile") or {}
        return {
            "id": s["cid"],
            "currentTitle": p.get("currentTitle"),
            "titles": (p.get("titles") or [])[:8],
            "totalYears": p.get("totalYears"),
            "skills": (p.get("skills") or [])[:40],
            "experience": [
                {"title": (e or {}).get("title"), "summary": ((e or {}).get("summary") or "")[:300]}
                for e in (p.get("experience") or [])[:6]
            ],
            "education": (p.get("education") or [])[:4],
            "certifications": (p.get("certifications") or [])[:6],
            # The deterministic scorer's findings — authoritative in the rubric.
            "missingMustHave": s["gaps"],
            "partialMustHave": s["breakdown"]["partialMustHave"],
        }

    judge_by_id: Dict[str, Dict[str, Any]] = {}
    if top and settings.MATCH_JUDGE_ENABLED:
        try:
            resp = await llm.judge_candidates(requirements, [_judge_view(s) for s in top])
            for item in (resp.get("candidates") or []):
                if item.get("id"):
                    judge_by_id[str(item["id"])] = item
        except Exception:  # noqa: BLE001 — judge is best-effort
            logger.warning("[Matching] judge step failed; keeping deterministic scores",
                           exc_info=True)
    for s in top:
        apply_judge(s, judge_by_id.get(s["cid"]))
    scored.sort(key=lambda x: (x["score"], x["breakdown"].get("base", 0.0)), reverse=True)

    # 7. assemble a full entry for EVERY scored candidate — not just the top N.
    # The recruiter can only judge whether the ranking is right by seeing what it
    # rejected and why, so the whole ranked list is persisted with its breakdown.
    def _entry(s: Dict[str, Any]) -> Dict[str, Any]:
        doc = s["doc"]
        profile = doc.get("profile") or {}
        contact = doc.get("contact") or {}
        rid = judge_by_id.get(s["cid"], {})
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
            "reasoning": "judge" if rid.get("reasons") else "deterministic",
            "judge": (s["breakdown"] or {}).get("judge"),
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
        "requirementsWarning": requirements_warning,
        "analysis": {
            "scoringVersion": SCORING_VERSION,
            "baseWeights": BASE_WEIGHTS,
            "judgeWeight": settings.MATCH_JUDGE_WEIGHT if settings.MATCH_JUDGE_ENABLED else 0.0,
            "reasonedTopN": reason_n,
            "candidates": all_entries,
            # Candidates that could not be scored — the run is honest about them
            # instead of pretending they were never considered.
            "errors": scoring_errors,
        },
        "modelVersions": {
            "extract": llm.extraction_version(),
            "embed": embeddings.embedding_version(),
            "reason": llm.reasoning_version(),
            "judge": llm.reasoning_version(),
            # Which OpenAI backend served this run — with `seed`, the pair that
            # makes a score drift between identical runs attributable.
            "systemFingerprint": llm.last_system_fingerprint(),
            "seed": settings.OPENAI_SEED,
        },
        "vectorBackend": settings.VECTOR_BACKEND,
        "createdAt": _now(),
    }
    match_run_id = str((await runs_col.insert_one(run_doc)).inserted_id)

    # 9. QA audit — same adversarial pass the pipeline engine runs. Verified
    # false negatives are corrected through the scorer and the persisted run is
    # updated; the response carries the corrected ranking. Fail-open by design.
    qa_summary: Optional[Dict[str, Any]] = None
    if settings.MATCH_QA_ENABLED and all_entries:
        from app.services import match_qa_service

        profiles_by_cid = {str(d["_id"]): (d.get("profile") or {}) for d in candidates}
        qa_summary = await match_qa_service.audit_run(
            db,
            match_run_id=match_run_id,
            pipeline_id=None,
            job_id=job_id,
            jd_title=requirements.get("title") or (jd_filename or "CV match"),
            requirements=requirements,
            entries=all_entries,
            profiles_by_cid=profiles_by_cid,
            sims_by_cid={cid: float(sim_by_id.get(cid, 0.0)) for cid in profiles_by_cid},
        )
        if qa_summary["status"] == "completed" and qa_summary["fnCorrected"]:
            all_entries.sort(
                key=lambda r: (r["score"], (r.get("breakdown") or {}).get("base", 0.0)),
                reverse=True,
            )
            results = all_entries[:return_top]
        await runs_col.update_one(
            {"_id": ObjectId(match_run_id)},
            {"$set": {"results": results, "analysis.candidates": all_entries,
                      "qa": qa_summary, "modelVersions.qa": match_qa_service.qa_model()}},
        )

    return {
        "matchRunId": match_run_id,
        "jdId": jd_id,
        "jdTitle": requirements.get("title"),
        "requirements": requirements,
        "requirementsWarning": requirements_warning,
        "candidatesConsidered": len(scored),
        "results": results,
        "qa": qa_summary,
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
