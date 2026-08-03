"""Sourcing-results auditor — does the search return who the recruiter asked for?

The complaint this answers, in the founder's words: "10 candidates for SAP HCM
are [supposedly] truly matching the criteria… but I could see the candidate in
India coming when Bavaria was wanted." Two very different failure modes hide in
that sentence, and they need two different tools:

  1. WRONG PLACE — a candidate in the wrong country. This is EXACT. It is caught
     upstream by a deterministic gate (candidate_pipeline._store_profiles via
     location_resolver.location_verdict) BEFORE this auditor runs — no LLM
     touches it, because "is India inside Germany" is arithmetic, not judgment,
     and a model would be slower, costlier and occasionally wrong.

  2. WRONG PERSON — a candidate in the right place whose profile isn't actually
     the specialty asked for: the keyword channel (which matches profile text,
     not the title line) can pull an "SAP FICO" or a generic "HR Business
     Partner" into an "SAP HCM" search. Whether a headline/role genuinely IS the
     target specialty is JUDGMENT, and that is this auditor's job. It uses the
     STRONGER QA_AUDITOR_MODEL (gpt-4o) — verification is harder than sourcing.

Discipline (shared with match_qa_service, same reasons):
  * The auditor sees the recruiter's query and each KEPT candidate's short
    profile, and judges off-specialty results on a confidence scale. The
    founder's standing instruction is zero wrong-specialty results in front of
    the recruiter, so this is authoritative: at/above SOURCING_QA_REJECT_CONFIDENCE
    the candidate is HIDDEN (isAccepted=False); between the flag floor and that
    it is annotated but shown; below the floor it is only recorded. Removal is
    deliberately gated on HIGH confidence so a hedged guess can't purge a real
    hire — "unsure → don't flag" is baked into the prompt and the floor.
  * Runs ONCE per search over the whole kept set → the bigger model is bounded.
  * Writes a ``qa_reports`` doc (kind="sourcing") for the admin QA page.
  * Fail-open: an auditor outage leaves the results untouched, status="skipped".
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.matching import llm_extraction_service as llm

logger = logging.getLogger(__name__)

COLLECTION = "qa_reports"


def _now() -> datetime:
    return datetime.utcnow()


def qa_model() -> str:
    return (settings.SOURCING_QA_MODEL or settings.QA_AUDITOR_MODEL
            or settings.REASONING_MODEL).strip()


_SOURCING_QA_SYSTEM = (
    "You are a QA auditor for a candidate SOURCING system. A recruiter ran a "
    "search for a specific role; the system returned a set of candidates. Your "
    "ONLY objective is to catch candidates that do NOT genuinely match the "
    "recruiter's target SPECIALTY or seniority — results that leaked in because "
    "a fuzzy keyword search matched some text on their profile. Confirming good "
    "matches earns you nothing; missing a bad one is your failure, but so is "
    "wrongly flagging a valid candidate (that discards a real hire).\n\n"
    "Judge SPECIALTY ONLY. Seniority/level (junior vs senior) and location are "
    "handled by separate deterministic systems — do NOT flag on either; a junior "
    "in the RIGHT specialty is not a mismatch here.\n\n"
    "What a MISMATCH is:\n"
    "* Different specialty within the same platform — e.g. target 'SAP HCM' "
    "(HR/payroll) but the candidate is 'SAP FICO'/'SAP FI-CA' (finance), "
    "'SAP IS-U' (utilities) or 'SAP Basis' (infra). Sharing the platform brand "
    "('SAP') is NOT enough.\n"
    "* A different profession entirely that merely name-drops a tool.\n\n"
    "What is NOT a mismatch (do not flag):\n"
    "* The right specialty phrased differently, in another language, or by a "
    "product name (SAP HCM ≡ SAP HR ≡ SAP SuccessFactors ≡ 'Personalabrechnung "
    "SAP'; PA/PY/OM are HCM sub-modules).\n"
    "* A generalist title whose specialization can't be told from the short "
    "profile — when unsure, do NOT flag (mark confidence low).\n"
    "* Junior/senior level, or location — other systems own these; ignore them.\n"
    "* **A DIFFERENT JOB FUNCTION IN THE RIGHT DOMAIN, when the query says so.** "
    "Read `roleFamily` and `domainTerms`. When `roleFamily` is 'commercial' the "
    "target is someone who SELLS in that domain, so 'Account Executive', 'Key "
    "Account Manager', 'Client Partner', 'Sales Director' or a bare 'Principal "
    "Consultant' are all CORRECT — flagging them as 'sales, not a consultant' is "
    "the exact error to avoid. The domain is what must match; the function word "
    "in the title is not evidence against them.\n\n"
    "Judge only from the title/headline/company text given. Never infer from a "
    "name, gender or photo. For each flagged candidate give the exact id, a "
    "one-line reason naming the specialty you think they actually are, and a "
    "confidence 0-1."
)

_SOURCING_QA_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "mismatches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "reason": {"type": "string"},
                    "likelyActualSpecialty": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["id", "reason", "likelyActualSpecialty", "confidence"],
            },
        }
    },
    "required": ["mismatches"],
}

# Below this confidence a mismatch is recorded but NOT surfaced as an active
# flag — the auditor's own hedge ("unsure → don't flag") made mechanical.
_FLAG_MIN_CONFIDENCE = 0.6


def _reject_confidence() -> float:
    # At/above this the auditor is confident enough to HIDE the candidate, not
    # just annotate — the recruiter's list stays free of wrong-specialty people.
    return float(getattr(settings, "SOURCING_QA_REJECT_CONFIDENCE", 0.8) or 0.8)


def _audit_sync(query: Dict[str, Any], candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    import json as _json

    user = (
        "The recruiter's search (what a returned candidate must genuinely be):\n"
        f"{_json.dumps(query, ensure_ascii=False)}\n\n"
        "Candidates the search returned (short profiles only):\n"
        f"{_json.dumps(candidates, ensure_ascii=False)}\n\n"
        "Return `mismatches` — only the candidates that are genuinely the wrong "
        "specialty/seniority. Empty list if every candidate is a plausible match."
    )
    return llm._chat_json(
        qa_model(), _SOURCING_QA_SYSTEM, user,
        schema_name="sourcing_qa", schema=_SOURCING_QA_SCHEMA,
        max_tokens=min(4096, 120 * max(1, len(candidates)) + 300),
        operation="sourcing_qa",
    )


async def audit_results(
    db,
    *,
    pipeline_id: str,
    job_id: str,
    jd_title: str,
    query: Dict[str, Any],
    kept: List[Dict[str, Any]],
    location_rejected: int = 0,
    domain_anchored: bool = False,
) -> Dict[str, Any]:
    """Audit the KEPT discovery results against the recruiter's query.

    `kept` is a list of {"candidateId", "title", "company", "location",
    "channels"} — the short profiles that passed the location + title gates and
    would be shown. Flags off-specialty candidates (annotates their candidate
    row + records them); never deletes. Returns the run-level summary and writes
    a qa_reports doc. Never raises.
    """
    started = _now()
    metrics = {
        "kept": len(kept),
        "locationRejected": int(location_rejected),
        "mismatchesRaised": 0,
        "mismatchesFlagged": 0,     # above the confidence floor
        "rejected": 0,              # high-confidence → hidden from the recruiter
        "lowConfidenceNoted": 0,
    }
    flags: List[Dict[str, Any]] = []
    status = "completed"
    error: Optional[str] = None

    try:
        if kept:
            batch = [
                {"id": str(k["candidateId"]),
                 "title": k.get("title") or "",
                 "company": k.get("company") or "",
                 "foundVia": k.get("channels") or []}
                for k in kept
            ]
            q = {k: query.get(k) for k in ("title", "targetTitles", "mustHaveSkills", "seniority")
                 if query.get(k)}
            resp = await asyncio.to_thread(_audit_sync, q, batch)
            raw = resp.get("mismatches") or []
            metrics["mismatchesRaised"] = len(raw)

            cands_col = db["candidates"]
            from bson import ObjectId
            for m in raw:
                conf = float(m.get("confidence") or 0.0)
                cid = str(m.get("id"))
                if conf < _FLAG_MIN_CONFIDENCE:
                    metrics["lowConfidenceNoted"] += 1
                    continue
                metrics["mismatchesFlagged"] += 1
                # A domain-anchored search was already verified server-side
                # against each profile's full text — evidence this auditor cannot
                # see, because it is shown only a title and a company. On that
                # input it cannot distinguish "wrong specialty" from "right
                # person, uninformative title", which is the normal case for
                # commercial roles (an Account Executive selling SAP Retail reads
                # as "a salesperson, not a consultant"). So it annotates and
                # ranks, but is not allowed to hide anyone.
                reject = conf >= _reject_confidence() and not domain_anchored
                flag = {
                    "candidateId": cid,
                    "reason": m.get("reason"),
                    "likelyActualSpecialty": m.get("likelyActualSpecialty"),
                    "confidence": conf,
                    "action": "rejected" if reject else "flagged",
                }
                flags.append(flag)
                qa_note = {
                    "reason": m.get("reason"),
                    "likelyActualSpecialty": m.get("likelyActualSpecialty"),
                    "confidence": conf, "at": _now(),
                    "action": "rejected" if reject else "flagged",
                }
                # High confidence → HIDE the candidate (isAccepted=False) so the
                # recruiter never sees a wrong-specialty result. Lower confidence
                # → annotate only. Both keep the QA evidence on the row.
                update: Dict[str, Any] = {"sourcingQaFlag": qa_note, "updatedAt": _now()}
                if reject:
                    metrics["rejected"] += 1
                    # Client-facing string: the recruiter sees this on the row.
                    # Name the specialty we believe they're actually in — never
                    # internal instrument language ("QA:"), same standard as the
                    # matching copy cleanup.
                    specialty = (m.get("likelyActualSpecialty")
                                 or m.get("reason") or "a different specialty")
                    update.update({
                        "isAccepted": False,
                        "rejectionReason": f"Different specialty: {specialty}",
                        "decidedAt": _now(),
                    })
                try:
                    await cands_col.update_one({"_id": ObjectId(cid)}, {"$set": update})
                except Exception as exc:  # noqa: BLE001 — writeback is best-effort
                    logger.warning("[SourcingQA] flag writeback failed for %s: %s", cid, exc)
    except llm.ExtractionError as e:
        status = "skipped"
        error = f"auditor model unavailable: {str(e)[:200]}"
        logger.warning("[SourcingQA] %s/%s skipped: %s", pipeline_id, job_id, error)
    except Exception as e:  # noqa: BLE001 — QA must never fail discovery
        status = "failed"
        error = str(e)[:300]
        logger.error("[SourcingQA] %s/%s audit failed: %s", pipeline_id, job_id, e, exc_info=True)

    report = {
        "kind": "sourcing",
        "pipelineId": pipeline_id,
        "jobId": job_id,
        "jdTitle": jd_title,
        "status": status,
        "error": error,
        "model": qa_model(),
        "metrics": metrics,
        "flags": flags,
        "startedAt": started,
        "createdAt": _now(),
    }
    report_id: Optional[str] = None
    try:
        report_id = str((await db[COLLECTION].insert_one(dict(report))).inserted_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("[SourcingQA] could not persist report: %s", e)

    return {
        "status": status, "reportId": report_id, "model": qa_model(),
        "kept": metrics["kept"], "locationRejected": metrics["locationRejected"],
        "mismatchesFlagged": metrics["mismatchesFlagged"],
        "rejected": metrics["rejected"],
        "at": _now(),
    }
