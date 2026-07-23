"""QA auditor — the adversarial second reader over every match run.

Why this exists (the incentive problem it solves)
-------------------------------------------------
The scoring chain is deliberately disciplined: the deterministic scorer's gap
list is AUTHORITATIVE, and the rubric judge is contractually forbidden from
contradicting it (FIT_RUBRIC's hard rules). That discipline is what keeps prose
from inflating scores — but it has a blind spot with a name: when the
deterministic evidence rules miss something a human would see ("SAP HCM" in an
experience bullet evidencing the must-have "SAP HR"), every downstream component
is REQUIRED to repeat the mistake. The observed production failure: a working
SAP-HCM specialist scored 16/100 with reasons reading "No evidence of SAP-HCM
experience despite current title as SAP-Spezialistin HCM."

The auditor is the one component whose objective is inverted: it earns credit
ONLY by catching scorer mistakes. It is shown the full evidence and the scorer's
verdicts and asked to find (a) false negatives — a skill called missing/partial
that the evidence supports, and (b) false positives — a credited skill or high
score the evidence does not support. Agreement earns it nothing.

Why the inverted incentive can't be gamed
-----------------------------------------
An agent rewarded for flagging would happily hallucinate flags. Two mechanical
checks referee it:
  * Every false-negative flag must include a VERBATIM quote from the candidate's
    evidence. The quote is string-checked against the evidence corpus
    (whitespace/case-normalised); a flag whose quote does not literally appear
    is discarded and counted against the auditor in the report.
  * A verified flag does not hand-edit the score. It is replayed through the
    real scorer (`_score_candidate(forced_credits=…)`), so coverage, ceiling and
    blend math stay the single source of truth and the corrected score is
    exactly as auditable as the original.

The asymmetry is deliberate and load-bearing:
  * Verified false negative → score corrected UPWARD (a wrongly rejected
    qualified candidate is unrecoverable — they are never enriched, never
    reviewed, never called).
  * False positive → ANNOTATION ONLY, never an automatic downgrade. Downgrading
    on an LLM's say-so would manufacture the exact false negative this module
    exists to prevent. The recruiter sees the flag and decides; the admin report
    counts it.

Everything the auditor does lands in a ``qa_reports`` doc (admin-only API) and a
``qa`` summary on the match run: flags raised, flags that survived verification,
flags discarded, scores corrected. Fail-open: if the auditor model is down the
run completes un-audited with ``qa.status="skipped"`` — a recruiter blocked by a
QA outage would route around the tool entirely, which protects nobody.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services import llm_extraction_service as llm
from app.services.matching_service import (
    _free_text_entries,
    _score_candidate,
    _skill_evidence_pool,
)

logger = logging.getLogger(__name__)

COLLECTION = "qa_reports"

# Evidence corpus cap per candidate — keeps the batch prompt bounded.
_MAX_EVIDENCE_CHARS = 6000
# Candidates per auditor LLM call. The audit reviews EVERY scored candidate, so
# a growing corpus grows the batch without bound — at ~6KB evidence each, an
# unbatched 100-candidate run would blow the model's context and the whole
# audit would fail-open, silently un-auditing exactly the big runs that need it
# most. Groups keep each call bounded; metrics aggregate across groups.
_AUDIT_BATCH_SIZE = 12
# A flag's quote must be at least this long to be checkable — a two-character
# "quote" would string-match half the corpus and verify nothing.
_MIN_QUOTE_LEN = 6


def _now() -> datetime:
    return datetime.utcnow()


def qa_model() -> str:
    """The match auditor's model: explicit override → shared auditor model.

    Verification is harder than the job, so this defaults to the stronger
    QA_AUDITOR_MODEL (gpt-4o), not the cheap worker REASONING_MODEL.
    """
    return (settings.MATCH_QA_MODEL or settings.QA_AUDITOR_MODEL
            or settings.REASONING_MODEL).strip()


# ── Mechanical quote verification ────────────────────────────────────────────

def _norm_for_quote(s: str) -> str:
    """Whitespace/case/punctuation-spacing normalisation for quote matching.

    The model reads evidence that we serialised through JSON; line breaks and
    bullet glyphs mutate along the way. Matching on a normalised form keeps a
    genuine quote from failing verification over a wrapped line, while still
    requiring the actual words in the actual order.
    """
    s = (s or "").lower()
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def verify_quote(quote: str, evidence_texts: List[str]) -> bool:
    """True when `quote` literally appears (normalised) in any evidence text."""
    q = _norm_for_quote(quote)
    if len(q) < _MIN_QUOTE_LEN:
        return False
    return any(q in _norm_for_quote(t) for t in evidence_texts if t)


# ── Auditor LLM call ─────────────────────────────────────────────────────────

_QA_SYSTEM = (
    "You are an independent QA auditor for a recruitment scoring system. You are "
    "NOT the scorer and you do not defend it: your only measure of success is the "
    "number of VERIFIED scoring mistakes you catch. Flags you raise are checked "
    "mechanically — a falseNegative flag counts only if its `quote` appears "
    "verbatim in the candidate evidence you were given; fabricated or paraphrased "
    "quotes are discarded and recorded as auditor errors. Agreeing with the scorer "
    "earns you nothing; missing a real mistake is your worst failure.\n\n"
    "Why this matters: a FALSE NEGATIVE (real evidence for a skill the scorer "
    "called missing) silently discards a qualified person — they are never "
    "enriched, never reviewed, never called. It is the costliest mistake this "
    "system can make. A FALSE POSITIVE (credited skill the evidence does not "
    "support) wastes a recruiter's interview slot and erodes trust in every "
    "other score.\n\n"
    "Rules:\n"
    "* Judge ONLY from the evidence text provided. Never infer from a name, "
    "photo, gender, age or employer prestige.\n"
    "* A falseNegative flag needs: the exact skill string from the requirements, "
    "a VERBATIM quote (copy the characters exactly, 6-160 chars) from that "
    "candidate's evidence that supports the skill, and one sentence explaining "
    "why the quote evidences it. Domain equivalences are in scope: e.g. SAP HCM "
    "is SAP's HR module family (PA/PY/OM are its sub-modules), so a profile "
    "working in 'SAP HCM PA, PY' evidences 'SAP HR'. Weak associations are not: "
    "'used a computer' does not evidence 'SAP'.\n"
    "* A falsePositive flag needs the credited skill string and one sentence on "
    "why the cited evidence does NOT support it (e.g. credit came from a fuzzy "
    "match to an unrelated term).\n"
    "* If the scorer's verdict for a candidate is sound, return empty lists for "
    "them — do not manufacture flags to look busy; unverifiable flags count "
    "against you."
)

_QA_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "falseNegatives": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "skill": {"type": "string"},
                                "quote": {"type": "string"},
                                "why": {"type": "string"},
                            },
                            "required": ["skill", "quote", "why"],
                        },
                    },
                    "falsePositives": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "skill": {"type": "string"},
                                "why": {"type": "string"},
                            },
                            "required": ["skill", "why"],
                        },
                    },
                },
                "required": ["id", "falseNegatives", "falsePositives"],
            },
        }
    },
    "required": ["candidates"],
}


def _audit_sync(requirements: Dict[str, Any], batch: List[Dict[str, Any]]) -> Dict[str, Any]:
    import json as _json

    user = (
        "Role requirements (the input query the scorer ran against):\n"
        f"{_json.dumps({k: requirements.get(k) for k in ('title', 'mustHaveSkills', 'niceToHaveSkills', 'minYears', 'location')}, ensure_ascii=False)}\n\n"
        "For each candidate below you get the scorer's verdict (score, creditedSkills "
        "with the rule that credited them, partialSkills, missingSkills) and the FULL "
        "evidence (short items + free text). Audit the verdict against the evidence.\n\n"
        f"{_json.dumps(batch, ensure_ascii=False)}\n\n"
        "Return one entry per candidate (same `id`) with falseNegatives and "
        "falsePositives (empty lists when the verdict is sound)."
    )
    return llm._chat_json(
        qa_model(), _QA_SYSTEM, user,
        schema_name="qa_audit", schema=_QA_SCHEMA,
        max_tokens=min(8192, 400 * max(1, len(batch)) + 300),
        operation="qa_audit",
    )


# ── Per-candidate audit input ────────────────────────────────────────────────

def _evidence_corpus(profile: Dict[str, Any]) -> List[str]:
    """The SAME evidence the scorer reads — pool items + free-text entries.

    Identity matters: verification checks quotes against this corpus, and a
    corpus wider than the scorer's would let QA 'verify' evidence the corrected
    rescore then can't see.
    """
    return _skill_evidence_pool(profile) + _free_text_entries(profile)


def _audit_view(entry: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """What the auditor sees for one candidate. Anonymised: no name/photo/contact."""
    bd = entry.get("breakdown") or {}
    skills_ev = []
    for c in bd.get("components", []):
        if c.get("key") == "skillCoverage":
            skills_ev = c.get("skills") or []
    corpus = _evidence_corpus(profile)
    total = 0
    bounded: List[str] = []
    for t in corpus:
        if total >= _MAX_EVIDENCE_CHARS:
            break
        t = str(t)[: _MAX_EVIDENCE_CHARS - total]
        bounded.append(t)
        total += len(t)
    return {
        "id": str(entry.get("candidateId")),
        "score": entry.get("score"),
        "creditedSkills": [
            {"skill": e["skill"], "rule": e.get("method"), "via": str(e.get("via"))[:120]}
            for e in skills_ev if (e.get("credit") or 0) >= 1.0
        ],
        "partialSkills": [
            {"skill": e["skill"], "credit": e.get("credit"), "via": str(e.get("via"))[:120]}
            for e in skills_ev if 0 < (e.get("credit") or 0) < 1.0
        ],
        "missingSkills": list(entry.get("gaps") or []),
        "totalYears": profile.get("totalYears"),
        "evidence": bounded,
    }


# ── The audit pass ───────────────────────────────────────────────────────────

async def audit_run(
    db,
    *,
    match_run_id: str,
    pipeline_id: Optional[str],
    job_id: Optional[str],
    jd_title: str,
    requirements: Dict[str, Any],
    entries: List[Dict[str, Any]],
    profiles_by_cid: Dict[str, Dict[str, Any]],
    sims_by_cid: Dict[str, float],
) -> Dict[str, Any]:
    """Audit every scored entry; correct verified false negatives IN PLACE.

    Mutates `entries` (score/breakdown/gaps/reasons + a `qa` block per corrected
    candidate), writes the qa_reports doc, and returns the run-level `qa`
    summary for the caller to stamp on the match run. Never raises: any internal
    failure degrades to {"status": "skipped"|"failed"} — the run completes.
    """
    started = _now()
    metrics = {
        "candidatesReviewed": 0,
        "fnFlagsRaised": 0,
        "fnFlagsVerified": 0,
        "fnFlagsDiscarded": 0,   # quote failed the mechanical check
        "fnCorrected": 0,        # verified AND the rescore moved the score up
        "fpFlagsRaised": 0,
    }
    per_candidate: List[Dict[str, Any]] = []
    corrections: List[Dict[str, Any]] = []
    status = "completed"
    error: Optional[str] = None

    try:
        auditable = [e for e in entries if str(e.get("candidateId")) in profiles_by_cid]
        metrics["candidatesReviewed"] = len(auditable)
        if auditable:
            batch = [
                _audit_view(e, profiles_by_cid[str(e["candidateId"])])
                for e in auditable
            ]
            # Bounded groups — one oversized call would fail the WHOLE audit open.
            by_id: Dict[str, Dict[str, Any]] = {}
            for i in range(0, len(batch), _AUDIT_BATCH_SIZE):
                resp = await asyncio.to_thread(
                    _audit_sync, requirements, batch[i:i + _AUDIT_BATCH_SIZE]
                )
                for c in (resp.get("candidates") or []):
                    by_id[str(c.get("id"))] = c

            must_set = {s for s in (requirements.get("mustHaveSkills") or []) if s}
            for entry in auditable:
                cid = str(entry["candidateId"])
                verdict = by_id.get(cid)
                if not verdict:
                    continue
                profile = profiles_by_cid[cid]
                corpus = _evidence_corpus(profile)

                fn_raised = [
                    f for f in (verdict.get("falseNegatives") or [])
                    # Only skills the run actually requires — the auditor can't
                    # "catch" a mistake on a requirement that doesn't exist.
                    if f.get("skill") in must_set
                ]
                fp_raised = [
                    f for f in (verdict.get("falsePositives") or [])
                    if f.get("skill") in must_set
                ]
                metrics["fnFlagsRaised"] += len(fn_raised)
                metrics["fpFlagsRaised"] += len(fp_raised)

                verified: Dict[str, Dict[str, Any]] = {}
                discarded: List[Dict[str, Any]] = []
                for f in fn_raised:
                    if verify_quote(f.get("quote") or "", corpus):
                        verified[f["skill"]] = {"quote": f.get("quote"), "why": f.get("why")}
                    else:
                        discarded.append(f)
                metrics["fnFlagsVerified"] += len(verified)
                metrics["fnFlagsDiscarded"] += len(discarded)

                cand_report: Dict[str, Any] = {
                    "candidateId": cid,
                    "fullName": entry.get("fullName"),
                    "originalScore": entry.get("score"),
                    "correctedScore": None,
                    "falseNegativesVerified": [
                        {"skill": s, **v} for s, v in verified.items()
                    ],
                    "falseNegativesDiscarded": [
                        {"skill": f.get("skill"), "quote": f.get("quote")} for f in discarded
                    ],
                    "falsePositives": fp_raised,
                }

                if verified:
                    corrected = _rescore_with_credits(
                        requirements, profile,
                        sims_by_cid.get(cid, 0.0), verified, entry,
                    )
                    if corrected is not None:
                        metrics["fnCorrected"] += 1
                        cand_report["correctedScore"] = corrected
                        corrections.append({
                            "candidateId": cid,
                            "fullName": entry.get("fullName"),
                            "from": cand_report["originalScore"],
                            "to": corrected,
                            "skills": sorted(verified.keys()),
                        })

                if fp_raised:
                    entry.setdefault("qa", {})["falsePositives"] = fp_raised

                if verified or fp_raised or discarded:
                    per_candidate.append(cand_report)
    except llm.ExtractionError as e:
        status = "skipped"
        error = f"auditor model unavailable: {str(e)[:200]}"
        logger.warning("[MatchQA] run %s skipped: %s", match_run_id, error)
    except Exception as e:  # noqa: BLE001 — QA must never fail the run
        status = "failed"
        error = str(e)[:300]
        logger.error("[MatchQA] run %s audit failed: %s", match_run_id, e, exc_info=True)

    report = {
        "kind": "match",
        "matchRunId": match_run_id,
        "pipelineId": pipeline_id,
        "jobId": job_id,
        "jdTitle": jd_title,
        "status": status,
        "error": error,
        "model": qa_model(),
        "metrics": metrics,
        "scoreCorrections": corrections,
        "perCandidate": per_candidate,
        "startedAt": started,
        "createdAt": _now(),
    }
    report_id: Optional[str] = None
    try:
        report_id = str((await db[COLLECTION].insert_one(dict(report))).inserted_id)
    except Exception as e:  # noqa: BLE001 — report persistence is best-effort
        logger.warning("[MatchQA] could not persist qa report for %s: %s", match_run_id, e)

    return {
        "status": status,
        "reportId": report_id,
        "model": qa_model(),
        "reviewed": metrics["candidatesReviewed"],
        "fnFlagsRaised": metrics["fnFlagsRaised"],
        "fnFlagsVerified": metrics["fnFlagsVerified"],
        "fnCorrected": metrics["fnCorrected"],
        "fpFlagsRaised": metrics["fpFlagsRaised"],
        "at": _now(),
    }


def _rescore_with_credits(
    requirements: Dict[str, Any],
    profile: Dict[str, Any],
    sim: float,
    verified: Dict[str, Dict[str, Any]],
    entry: Dict[str, Any],
) -> Optional[float]:
    """Replay the deterministic scorer with QA-verified credits; mutate `entry`
    if — and only if — the score IMPROVES. Returns the new score, else None.

    The judge blend is deliberately dropped from a corrected entry: the judge's
    verdict was computed under the wrong gap list (its hard rules FORCED it to
    score the candidate as missing those skills), so blending it back in would
    re-poison the corrected number. `reasoning` flips to "qa_corrected" and the
    old judge verdict stays in `qa.previousJudge` for the audit trail.
    """
    score, subscores, gaps, breakdown = _score_candidate(
        requirements, profile, sim, forced_credits=verified,
    )
    old_score = float(entry.get("score") or 0.0)
    if score <= old_score:
        return None

    entry.setdefault("qa", {})
    entry["qa"].update({
        "corrected": True,
        "originalScore": old_score,
        "verifiedSkills": [
            {"skill": s, "quote": (v.get("quote") or "")[:160]} for s, v in verified.items()
        ],
        "previousJudge": entry.get("judge"),
    })
    entry["score"] = score
    entry["subscores"] = subscores
    entry["gaps"] = gaps
    entry["breakdown"] = breakdown
    entry["partial"] = breakdown["partialMustHave"]
    entry["judge"] = None
    entry["reasoning"] = "qa_corrected"
    skills_list = ", ".join(sorted(verified.keys()))
    # Client-facing copy — states what was found, never the audit mechanics (no
    # "QA", no before/after numbers). A recruiter reading "corrected from 46.5 to
    # 100" has no way to read that as anything but "the tool was wrong until we
    # caught it", which manufactures doubt about the score sitting right next to
    # it. The old/new numbers and the "qa_corrected" reasoning tag are still fully
    # recorded on `entry["qa"]` and the admin-only qa_reports doc for the audit
    # trail — this list is the only thing a client ever sees.
    entry["reasons"] = [
        f"Their profile provides direct evidence for: {skills_list}.",
        *[r for r in (entry.get("reasons") or []) if r][:2],
    ]
    return score
