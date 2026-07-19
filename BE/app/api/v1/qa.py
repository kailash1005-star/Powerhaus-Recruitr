"""QA report API — admin-only visibility into the match-QA auditor.

The reports quantify the system's own mistakes (false negatives caught and
corrected, false positives flagged) per match run. They are an internal quality
instrument for the operators, NOT a client-facing feature: a beta client reads
"the tool corrected itself 3 times" very differently from the engineers who
built the correction loop. Hence the hard admin gate on every route.

Gate: principal email ∈ settings.ADMIN_EMAILS (comma-separated, case-
insensitive) OR the 'admin' role claim. Local dev (AUTH_ENABLED=false) uses
DEV_PRINCIPAL, which carries the admin role — the page works on localhost.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.database import get_collection
from app.security.deps import Principal, require_auth

logger = logging.getLogger(__name__)

router = APIRouter()


def admin_email_set() -> set:
    return {e.strip().lower() for e in (settings.ADMIN_EMAILS or "").split(",") if e.strip()}


def admin_sub_set() -> set:
    # `sub` is case-sensitive (it's an opaque id), so no lower-casing here.
    return {s.strip() for s in (settings.ADMIN_SUBS or "").split(",") if s.strip()}


def is_admin(principal: Principal) -> bool:
    if principal.has_role("admin"):
        return True
    # Sub-based allowlist works even when the Auth0 Action that stamps the email
    # claim isn't firing — `sub` is always present in a verified token.
    if principal.sub and principal.sub in admin_sub_set():
        return True
    email = (principal.email or "").strip().lower()
    return bool(email) and email in admin_email_set()


async def require_admin(principal: Principal = Depends(require_auth)) -> Principal:
    """403 for everyone who isn't on the operator allowlist.

    403, not 404: the UI uses this signal to hide the nav item, and a
    distinguishable status makes that logic honest. The report CONTENT is
    what's sensitive, not the existence of the endpoint.
    """
    if not is_admin(principal):
        raise HTTPException(status_code=403, detail="admin access required")
    return principal


@router.get("/access")
async def qa_access(principal: Principal = Depends(require_auth)) -> Dict[str, Any]:
    """Cheap probe for the UI: should the QA nav item render for this user?"""
    return {"isAdmin": is_admin(principal)}


@router.get("/reports", dependencies=[Depends(require_admin)])
async def list_reports(limit: int = 50) -> Dict[str, Any]:
    """Newest-first run-wise QA summaries + lifetime totals for the header."""
    limit = max(1, min(int(limit or 50), 200))
    col = await get_collection("qa_reports")

    items: List[Dict[str, Any]] = []
    # Match-auditor totals + sourcing-auditor totals, kept separate: they count
    # different events (score corrections vs off-specialty flags + location
    # rejections) and blending them would make both meaningless.
    totals = {"runs": 0, "candidatesReviewed": 0, "fnFlagsRaised": 0,
              "fnFlagsVerified": 0, "fnCorrected": 0, "fpFlagsRaised": 0,
              "fnFlagsDiscarded": 0}
    sourcing_totals = {"runs": 0, "kept": 0, "locationRejected": 0,
                       "mismatchesFlagged": 0}
    async for doc in col.find({}).sort("createdAt", -1):
        m = doc.get("metrics") or {}
        kind = doc.get("kind") or "match"
        if kind == "sourcing":
            sourcing_totals["runs"] += 1
            for k in ("kept", "locationRejected", "mismatchesFlagged"):
                sourcing_totals[k] += int(m.get(k) or 0)
        else:
            totals["runs"] += 1
            for k in ("candidatesReviewed", "fnFlagsRaised", "fnFlagsVerified",
                      "fnCorrected", "fpFlagsRaised", "fnFlagsDiscarded"):
                totals[k] += int(m.get(k) or 0)
        if len(items) < limit:
            items.append({
                "id": str(doc["_id"]),
                "kind": kind,
                "matchRunId": doc.get("matchRunId"),
                "pipelineId": doc.get("pipelineId"),
                "jobId": doc.get("jobId"),
                "jdTitle": doc.get("jdTitle"),
                "status": doc.get("status"),
                "model": doc.get("model"),
                "metrics": m,
                "scoreCorrections": doc.get("scoreCorrections") or [],
                "flags": doc.get("flags") or [],
                "createdAt": (doc.get("createdAt").isoformat() + "Z"
                              if doc.get("createdAt") else None),
            })
    return {"totals": totals, "sourcingTotals": sourcing_totals, "reports": items}


@router.get("/reports/{report_id}", dependencies=[Depends(require_admin)])
async def get_report(report_id: str) -> Dict[str, Any]:
    col = await get_collection("qa_reports")
    try:
        doc = await col.find_one({"_id": ObjectId(report_id)})
    except Exception:  # noqa: BLE001 — malformed id is a missing report
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="report not found")
    doc["id"] = str(doc.pop("_id"))
    for k in ("createdAt", "startedAt"):
        if doc.get(k):
            doc[k] = doc[k].isoformat() + "Z"
    return doc
