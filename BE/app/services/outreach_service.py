"""
Outreach CRM service — the engine behind Outreach → Leads / Candidates.

Responsibilities:
  • ingest_event()   — apply a CanonicalEvent idempotently: resolve/create the
    message row, append the audit event (unique providerEventId), and roll the
    derived funnel status forward (never backward).
  • list_messages()  — the paginated read model the UI renders.
  • metrics()        — funnel counts per audience for the KPI strip + dashboard.
  • enroll()         — push a lead/candidate into the sending campaign.
  • classify_reply() — best-effort LLM tag for replies (interested / OOO / opt-out).

Status precedence lives in models.outreach.STAGE_RANK so an open can never
overwrite a reply, and bounced/unsubscribed are terminal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.models.outreach import (
    ACTIVITY_LABEL,
    STAGE_RANK,
    TERMINAL_TYPES,
)
from app.services.outreach_provider import CanonicalEvent, get_sender

logger = logging.getLogger(__name__)

MESSAGES = "outreach_messages"
EVENTS = "outreach_events"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tenant() -> str:
    return settings.OUTREACH_TENANT_ID or "default"


def outreach_configured() -> bool:
    """True when we can actually enroll/send (Smartlead key present)."""
    return bool(settings.SMARTLEAD_API_KEY)


def _maybe_oid(value: Any) -> Optional[ObjectId]:
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


def _dedupe_key(ev: CanonicalEvent) -> str:
    """Stable natural id so duplicate webhooks resolve to the same row."""
    email = (ev.email or "unknown").lower().strip()
    if ev.provider == "calcom":
        return f"calcom:{email}"
    return f"smartlead:{ev.campaign_id or 'default'}:{email}"


# ── Resolve / create the message row ─────────────────────────────────────────
async def _resolve_message(db, ev: CanonicalEvent) -> Dict[str, Any]:
    col = db[MESSAGES]
    tenant = _tenant()

    # 1. Explicit echo of our messageId (strongest signal).
    oid = _maybe_oid(ev.message_ref)
    if oid:
        doc = await col.find_one({"_id": oid})
        if doc:
            return doc

    # 2. A meeting (Cal.com) should attach to whatever row we already track for
    #    that email, regardless of which provider created it.
    if ev.provider == "calcom" and ev.email:
        existing = await col.find_one(
            {"tenantId": tenant, "email": ev.email.lower().strip()},
            sort=[("lastActivityAt", -1)],
        )
        if existing:
            return existing

    # 3. Natural dedupe key → find-or-create (idempotent upsert).
    key = _dedupe_key(ev)
    contact = ev.contact or {}
    audience = ev.audience or ("lead" if ev.provider != "calcom" else "lead")
    set_on_insert = {
        "tenantId": tenant,
        "audience": audience,
        "dedupeKey": key,
        "contactName": contact.get("name"),
        "email": (ev.email or "").lower().strip() or None,
        "title": contact.get("title"),
        "company": contact.get("company"),
        "roleTitle": ev.campaign_name if audience == "candidate" else None,
        "channel": "Email",
        "provider": ev.provider if ev.provider != "calcom" else "smartlead",
        "providerCampaignId": ev.campaign_id,
        "providerLeadId": ev.provider_lead_id,
        "campaignName": ev.campaign_name,
        "status": "sent",
        "stageRank": 0,
        "flags": {"bounced": False, "unsubscribed": False},
        "replyClass": None,
        "sentAt": ev.occurred_at,
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    doc = await col.find_one_and_update(
        {"tenantId": tenant, "dedupeKey": key},
        {"$setOnInsert": set_on_insert},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc


def _compute_status(msg: Dict[str, Any], ev: CanonicalEvent) -> Dict[str, Any]:
    """Roll funnel status forward; apply terminal flags. Returns $set fields."""
    flags = dict(msg.get("flags") or {"bounced": False, "unsubscribed": False})
    cur_rank = int(msg.get("stageRank") or 0)
    cur_status = msg.get("status") or "sent"

    set_fields: Dict[str, Any] = {
        "updatedAt": _now(),
        "lastActivityAt": ev.occurred_at,
        "lastActivity": ACTIVITY_LABEL.get(ev.type, ev.type.title()),
    }

    if ev.type in TERMINAL_TYPES:
        flags[ev.type] = True
        set_fields["flags"] = flags
        # unsubscribed outranks bounced for display
        set_fields["status"] = "unsubscribed" if flags.get("unsubscribed") else "bounced"
        return set_fields

    rank = STAGE_RANK.get(ev.type, 0)
    if rank > cur_rank:
        set_fields["stageRank"] = rank
        # Don't visually downgrade a terminal state, but do record progress.
        set_fields["status"] = ev.type if cur_status not in TERMINAL_TYPES else cur_status
    set_fields["flags"] = flags
    return set_fields


async def ingest_event(db, ev: CanonicalEvent) -> Dict[str, Any]:
    """Idempotently apply one canonical event. Safe to call from webhook AND
    reconcile paths — the unique providerEventId dedupes."""
    if ev.type not in (set(STAGE_RANK) | TERMINAL_TYPES):
        return {"ok": False, "reason": "invalid_type"}

    msg = await _resolve_message(db, ev)
    msg_id = msg["_id"]

    # Append to the audit log first; duplicate = already processed → stop.
    try:
        await db[EVENTS].insert_one({
            "tenantId": _tenant(),
            "messageId": msg_id,
            "type": ev.type,
            "provider": ev.provider,
            "providerEventId": ev.provider_event_id,
            "occurredAt": ev.occurred_at,
            "payload": ev.raw or {},
            "createdAt": _now(),
        })
    except DuplicateKeyError:
        return {"ok": True, "deduped": True, "messageId": str(msg_id)}

    set_fields = _compute_status(msg, ev)

    # Reply classification (best-effort, non-blocking on failure).
    if ev.type == "replied" and ev.reply_text:
        try:
            set_fields["replyClass"] = await classify_reply(ev.reply_text)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Outreach] reply classification failed: %s", e)

    await db[MESSAGES].update_one({"_id": msg_id}, {"$set": set_fields})
    return {"ok": True, "messageId": str(msg_id), "status": set_fields.get("status")}


# ── Read model ───────────────────────────────────────────────────────────────
def _to_row(doc: Dict[str, Any]) -> Dict[str, Any]:
    audience = doc.get("audience") or "lead"
    secondary = doc.get("company") if audience == "lead" else doc.get("roleTitle")
    last_at = doc.get("lastActivityAt")
    sent_at = doc.get("sentAt")
    return {
        "id": str(doc.get("_id")),
        "name": doc.get("contactName") or (doc.get("email") or "Unknown"),
        "secondary": secondary or "—",
        "title": doc.get("title") or "",
        "email": doc.get("email") or "",
        "channel": doc.get("channel") or "Email",
        "status": doc.get("status") or "sent",
        "replyClass": doc.get("replyClass"),
        "lastActivity": doc.get("lastActivity") or "—",
        "lastActivityAt": last_at.isoformat() if isinstance(last_at, datetime) else None,
        "sentAt": sent_at.isoformat() if isinstance(sent_at, datetime) else None,
    }


async def list_messages(
    db, audience: str, status: Optional[str], page: int, limit: int
) -> Dict[str, Any]:
    q: Dict[str, Any] = {"tenantId": _tenant(), "audience": audience}
    if status and status != "all":
        q["status"] = status
    col = db[MESSAGES]
    total = await col.count_documents(q)
    skip = (page - 1) * limit
    cursor = col.find(q).sort("lastActivityAt", -1).skip(skip).limit(limit)
    items = [_to_row(doc) async for doc in cursor]
    return {"total": total, "page": page, "limit": limit, "items": items}


async def metrics(db, audience: str) -> Dict[str, int]:
    """Funnel counts for one audience (cumulative — replied implies opened)."""
    base = {"tenantId": _tenant(), "audience": audience}
    col = db[MESSAGES]
    total = await col.count_documents(base)
    opened = await col.count_documents({**base, "stageRank": {"$gte": STAGE_RANK["opened"]}})
    replied = await col.count_documents({**base, "stageRank": {"$gte": STAGE_RANK["replied"]}})
    meetings = await col.count_documents({**base, "stageRank": {"$gte": STAGE_RANK["meeting"]}})
    unsub = await col.count_documents({**base, "flags.unsubscribed": True})
    bounced = await col.count_documents({**base, "flags.bounced": True})
    return {
        "total": total,
        "opened": opened,
        "replied": replied,
        "meetings": meetings,
        "unsubscribed": unsub,
        "bounced": bounced,
    }


# ── Enrollment (sending side) ────────────────────────────────────────────────
async def enroll(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create/update a message row and push the contact into the sender campaign."""
    sender = get_sender()
    tenant = _tenant()
    email = (payload.get("email") or "").lower().strip()
    if not email:
        raise ValueError("email is required to enroll")
    audience = payload.get("audience") or "lead"
    campaign_id = payload.get("campaignId") or settings.SMARTLEAD_DEFAULT_CAMPAIGN_ID or None
    key = f"smartlead:{campaign_id or 'default'}:{email}"

    doc = await db[MESSAGES].find_one_and_update(
        {"tenantId": tenant, "dedupeKey": key},
        {"$setOnInsert": {
            "tenantId": tenant,
            "audience": audience,
            "dedupeKey": key,
            "contactName": payload.get("name"),
            "email": email,
            "title": payload.get("title"),
            "company": payload.get("company"),
            "roleTitle": payload.get("roleTitle"),
            "channel": "Email",
            "provider": "smartlead",
            "providerCampaignId": campaign_id,
            "campaignName": payload.get("campaignName") or payload.get("roleTitle"),
            "leadId": payload.get("leadId"),
            "candidateId": payload.get("candidateId"),
            "status": "sent",
            "stageRank": STAGE_RANK["sent"],
            "flags": {"bounced": False, "unsubscribed": False},
            "sentAt": _now(),
            "lastActivity": "Enrolled",
            "lastActivityAt": _now(),
            "createdAt": _now(),
        }, "$set": {"updatedAt": _now()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    # Graceful degrade: if the sender isn't configured yet, we still TRACK the
    # contact in the CRM (status "sent") so the workflow is demonstrable; actual
    # delivery starts once SMARTLEAD_API_KEY is added. Never lose the row.
    if not outreach_configured():
        await db[MESSAGES].update_one(
            {"_id": doc["_id"]},
            {"$set": {"lastActivity": "Queued — sending not connected", "updatedAt": _now()}},
        )
        return {
            "messageId": str(doc["_id"]), "tracked": True, "sent": False,
            "note": "Tracked in Outreach. Connect Smartlead (SMARTLEAD_API_KEY) to start delivery.",
        }

    try:
        result = await sender.enroll({**doc, "_id": str(doc["_id"])})
    except Exception as e:  # noqa: BLE001
        logger.warning("[Outreach] provider enroll failed, kept as tracked: %s", e)
        return {"messageId": str(doc["_id"]), "tracked": True, "sent": False, "note": str(e)}

    await db[MESSAGES].update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "providerCampaignId": result.get("providerCampaignId"),
            "providerLeadId": result.get("providerLeadId"),
            "updatedAt": _now(),
        }},
    )
    return {"messageId": str(doc["_id"]), "tracked": True, "sent": True, **result}


# ── Reply classification (best-effort LLM) ───────────────────────────────────
_REPLY_SYSTEM = (
    "Classify a candidate/HR reply to a recruiting outreach email into exactly one "
    "label: 'interested', 'not_interested', 'ooo' (out-of-office/auto-reply), or "
    "'opt_out' (asks to stop/unsubscribe). Return ONLY the label."
)


async def classify_reply(text: str) -> Optional[str]:
    if not settings.OPENAI_API_KEY or not text:
        return None
    import asyncio

    def _run() -> Optional[str]:
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=settings.EXTRACTION_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": _REPLY_SYSTEM},
                {"role": "user", "content": text[:2000]},
            ],
        )
        label = (resp.choices[0].message.content or "").strip().lower()
        valid = {"interested", "not_interested", "ooo", "opt_out"}
        return label if label in valid else None

    return await asyncio.to_thread(_run)
