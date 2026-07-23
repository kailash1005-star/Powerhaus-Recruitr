"""GDPR data-protection service — erasure (Art. 17), access/portability (Art. 15
& 20) and a processing audit trail (accountability, Art. 5(2)).

The product stores scraped LinkedIn/Apollo PII (candidates, prospects, uploaded
CVs and the enrichment cache) that data subjects did not directly consent to.
Three obligations follow and live here:

  * ERASE  — hard-delete everything held about one person, across every
             collection, on request.
  * EXPORT — return everything held about one person (a DSAR response).
  * AUDIT  — record every erase/export (who, when, what, how many) in an
             append-only ``processing_audit`` log, so the controller can show
             what was done with personal data.

Every operation is TENANT-SCOPED: a non-admin caller can only erase or export
data their own tenant holds. Admins (operators acting for the controller) act
across tenants. A subject is identified by any of: candidate id, cv id,
prospect id, email, or LinkedIn URL/slug.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bson import ObjectId

logger = logging.getLogger(__name__)

AUDIT = "processing_audit"

# Collections that hold candidate/subject PII, with the field(s) that carry an
# email and a linkedin identifier so a subject can be matched across all of them.
_PII_COLLECTIONS = ("candidates", "prospects", "cv_candidates")


def _now() -> datetime:
    return datetime.utcnow()


async def ensure_indexes(db) -> None:
    """Append-only audit log — queried by subject and by time."""
    try:
        col = db[AUDIT]
        await col.create_index("occurredAt", name="idx_audit_occurredAt")
        await col.create_index([("tenantId", 1), ("subjectKey", 1)], name="idx_audit_subject")
    except Exception as e:  # noqa: BLE001 — audit indexing must never block boot
        logger.warning("[GDPR] could not ensure audit indexes: %s", e)


async def record_processing(
    db, *, action: str, actor: Dict[str, Any], tenant_id: Optional[str],
    subject: Dict[str, Any], details: Optional[Dict[str, Any]] = None,
) -> None:
    """Write one immutable audit entry. Best-effort — a failed audit write is
    logged but never sinks the operation it records (that would be worse)."""
    try:
        await db[AUDIT].insert_one({
            "action": action,                       # "erase" | "export"
            "occurredAt": _now(),
            "actorSub": actor.get("sub"),
            "actorEmail": actor.get("email"),
            "actorIsAdmin": bool(actor.get("is_admin")),
            "tenantId": tenant_id,
            "subjectKey": _subject_key(subject),
            "subject": subject,
            "details": details or {},
        })
    except Exception as e:  # noqa: BLE001
        logger.error("[GDPR] audit write failed for %s: %s", action, e)


def _subject_key(subject: Dict[str, Any]) -> str:
    for k in ("candidateId", "cvId", "prospectId", "email", "linkedinUrl"):
        if subject.get(k):
            return f"{k}:{subject[k]}"
    return "unknown"


def _linkedin_slug(url_or_slug: str) -> str:
    s = (url_or_slug or "").strip().lower().rstrip("/")
    m = re.search(r"/in/([^/?#]+)", s)
    return (m.group(1) if m else s.split("/")[-1]).strip()


async def _match_filters(db, tenant_scope: Dict[str, Any], subject: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build a per-collection Mongo filter that selects the subject's docs, each
    already restricted to ``tenant_scope`` (empty for admins). Only the
    identifiers present on ``subject`` are used."""
    email = (subject.get("email") or "").strip().lower()
    linkedin = _linkedin_slug(subject.get("linkedinUrl") or "") if subject.get("linkedinUrl") else ""

    def _id_or(col_key: str, id_val: str) -> List[dict]:
        try:
            return [{"_id": ObjectId(id_val)}]
        except Exception:
            return []

    ors_by_col: Dict[str, List[dict]] = {c: [] for c in _PII_COLLECTIONS}

    if subject.get("candidateId"):
        ors_by_col["candidates"] += _id_or("candidates", subject["candidateId"])
    if subject.get("prospectId"):
        ors_by_col["prospects"] += _id_or("prospects", subject["prospectId"])
    if subject.get("cvId"):
        ors_by_col["cv_candidates"] += _id_or("cv_candidates", subject["cvId"])

    if email:
        # Emails live on different paths per collection.
        ors_by_col["candidates"] += [
            {"enrichedData.email": email}, {"apifyEnrichment.contact.email": email}, {"email": email}]
        ors_by_col["prospects"] += [{"email": email}]
        ors_by_col["cv_candidates"] += [{"contact.email": email}, {"profile.email": email}]
    if linkedin:
        ors_by_col["candidates"] += [
            {"linkedinSlug": linkedin}, {"apifyEnrichment.profile.publicIdentifier": linkedin}]
        ors_by_col["prospects"] += [{"prospectDetails.linkedinUrl": {"$regex": re.escape(linkedin) + r"/?$"}}]
        ors_by_col["cv_candidates"] += [{"profile.linkedinUrl": {"$regex": re.escape(linkedin) + r"/?$"}}]

    out: Dict[str, Dict[str, Any]] = {}
    for col, ors in ors_by_col.items():
        if not ors:
            continue
        f: Dict[str, Any] = {"$or": ors}
        if tenant_scope:
            f = {"$and": [tenant_scope, f]}
        out[col] = f
    return out


async def export_subject(db, *, ctx, subject: Dict[str, Any]) -> Dict[str, Any]:
    """DSAR: return every PII record held about the subject, tenant-scoped."""
    tenant_scope = {} if ctx.is_admin else {"tenantId": ctx.tenant_id}
    filters = await _match_filters(db, tenant_scope, subject)
    records: Dict[str, List[dict]] = {}
    for col, f in filters.items():
        docs = []
        async for d in db[col].find(f):
            d["_id"] = str(d["_id"])
            docs.append(d)
        if docs:
            records[col] = docs
    await record_processing(
        db, action="export",
        actor={"sub": ctx.sub, "email": ctx.email, "is_admin": ctx.is_admin},
        tenant_id=None if ctx.is_admin else ctx.tenant_id, subject=subject,
        details={"counts": {c: len(v) for c, v in records.items()}},
    )
    return {"subject": subject, "records": records,
            "counts": {c: len(v) for c, v in records.items()}}


async def erase_subject(db, *, ctx, subject: Dict[str, Any]) -> Dict[str, Any]:
    """Right-to-erasure: hard-delete the subject's PII across all collections,
    plus their CV file bytes and enrichment-cache entries, tenant-scoped."""
    tenant_scope = {} if ctx.is_admin else {"tenantId": ctx.tenant_id}
    filters = await _match_filters(db, tenant_scope, subject)

    deleted: Dict[str, int] = {}
    # Capture ids BEFORE deleting so we can cascade to side collections keyed by _id.
    cv_ids: List[ObjectId] = []
    if "cv_candidates" in filters:
        async for d in db["cv_candidates"].find(filters["cv_candidates"], {"_id": 1}):
            cv_ids.append(d["_id"])

    for col, f in filters.items():
        res = await db[col].delete_many(f)
        if res.deleted_count:
            deleted[col] = res.deleted_count

    # Cascade: raw CV bytes live in cv_files keyed by the cv_candidates _id.
    if cv_ids:
        res = await db["cv_files"].delete_many({"_id": {"$in": cv_ids}})
        if res.deleted_count:
            deleted["cv_files"] = res.deleted_count

    # Enrichment cache holds the verbatim scraped profile keyed by public id.
    linkedin = _linkedin_slug(subject.get("linkedinUrl") or "") if subject.get("linkedinUrl") else ""
    if linkedin:
        res = await db["profileEnrichmentCache"].delete_many(
            {"_id": {"$regex": re.escape(linkedin), "$options": "i"}})
        if res.deleted_count:
            deleted["profileEnrichmentCache"] = res.deleted_count

    await record_processing(
        db, action="erase",
        actor={"sub": ctx.sub, "email": ctx.email, "is_admin": ctx.is_admin},
        tenant_id=None if ctx.is_admin else ctx.tenant_id, subject=subject,
        details={"deleted": deleted},
    )
    return {"subject": subject, "deleted": deleted,
            "totalDeleted": sum(deleted.values())}


async def apply_retention(db, retention_days: int) -> None:
    """Boot-time sweep: hard-delete scraped PII older than the retention window.

    Disabled when ``retention_days <= 0`` (the default until a lawful window is
    agreed with the client). Never raises — a retention sweep that blocks boot is
    worse than one that runs late."""
    if not retention_days or retention_days <= 0:
        return
    try:
        cutoff = _now() - timedelta(days=retention_days)
        total = 0
        for col in _PII_COLLECTIONS:
            res = await db[col].delete_many({"createdAt": {"$lt": cutoff}})
            total += res.deleted_count
        if total:
            logger.warning("[GDPR] retention sweep erased %d PII record(s) older than %dd",
                           total, retention_days)
    except Exception as e:  # noqa: BLE001
        logger.warning("[GDPR] retention sweep skipped: %s", e)
