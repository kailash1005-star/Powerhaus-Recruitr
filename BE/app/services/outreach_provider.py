"""
Outreach provider adapters.

A thin, swappable interface over the deliverability/sending platform — exactly
the same pattern as `vector_store.get_vector_store(...)`. The CRM and ingestion
layer speak only `CanonicalEvent`; each provider knows how to:

  • verify_signature(...) — authenticate an inbound webhook,
  • normalize(...)        — map a raw webhook payload → CanonicalEvent(s),
  • enroll(...)           — push a contact into a sending campaign (sending side).

First adapter: Smartlead (chosen). Cal.com is a meetings-only source adapter
(it can't send, only report `meeting`). Adding Instantly later = one new class.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from app.config import settings
from app.models.outreach import VALID_EVENT_TYPES

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime:
    """Best-effort timestamp parse → aware UTC datetime."""
    if value is None:
        return _now()
    if isinstance(value, (int, float)):
        # epoch seconds or millis
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:  # noqa: BLE001
            return _now()
    if isinstance(value, str):
        s = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return _now()
    return _now()


@dataclass
class CanonicalEvent:
    """Provider-agnostic outreach event the ingestion layer understands."""
    type: str                                  # one of VALID_EVENT_TYPES
    provider: str
    provider_event_id: str
    occurred_at: datetime
    email: Optional[str] = None
    contact: Dict[str, Any] = field(default_factory=dict)
    campaign_id: Optional[str] = None
    campaign_name: Optional[str] = None
    audience: Optional[str] = None             # "lead" | "candidate" (if echoed)
    message_ref: Optional[str] = None          # our messageId, if echoed back
    provider_lead_id: Optional[str] = None
    reply_text: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class OutreachProvider:
    name = "base"

    def verify_signature(self, headers: Dict[str, str], raw_body: bytes) -> bool:
        raise NotImplementedError

    def normalize(self, body: Dict[str, Any]) -> List[CanonicalEvent]:
        raise NotImplementedError

    async def enroll(self, message: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


# ── Smartlead ────────────────────────────────────────────────────────────────
_SMARTLEAD_EVENT_MAP = {
    "EMAIL_SENT": "sent",
    "SENT": "sent",
    "EMAIL_DELIVERED": "delivered",
    "EMAIL_OPEN": "opened",
    "OPEN": "opened",
    "EMAIL_OPENED": "opened",
    "EMAIL_LINK_CLICK": "clicked",
    "CLICK": "clicked",
    "EMAIL_CLICKED": "clicked",
    "EMAIL_REPLY": "replied",
    "REPLY": "replied",
    "EMAIL_REPLIED": "replied",
    "EMAIL_BOUNCE": "bounced",
    "BOUNCE": "bounced",
    "EMAIL_BOUNCED": "bounced",
    "LEAD_UNSUBSCRIBED": "unsubscribed",
    "UNSUBSCRIBED": "unsubscribed",
    "LEAD_UNSUBSCRIBE": "unsubscribed",
}


class SmartleadProvider(OutreachProvider):
    name = "smartlead"

    def verify_signature(self, headers: Dict[str, str], raw_body: bytes) -> bool:
        secret = settings.SMARTLEAD_WEBHOOK_SECRET
        if not secret:
            return True  # verification disabled — accept (logged by caller)
        # Accept either an HMAC header or a shared secret echoed in the body.
        hdr = (
            headers.get("x-smartlead-signature")
            or headers.get("x-webhook-signature")
            or ""
        ).strip()
        if hdr:
            digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(digest, hdr.replace("sha256=", "")):
                return True
        # Fallback: Smartlead lets you put a secret in the webhook payload.
        try:
            import json
            data = json.loads(raw_body or b"{}")
            echoed = str(data.get("secret_key") or data.get("webhook_secret") or "")
            if echoed and hmac.compare_digest(echoed, secret):
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def normalize(self, body: Dict[str, Any]) -> List[CanonicalEvent]:
        raw_type = str(
            body.get("event_type") or body.get("type") or body.get("event") or ""
        ).upper()
        etype = _SMARTLEAD_EVENT_MAP.get(raw_type)
        if etype not in VALID_EVENT_TYPES:
            logger.info("[Smartlead] ignoring unmapped event_type=%s", raw_type)
            return []

        lead = body.get("lead") or {}
        custom = lead.get("custom_fields") or body.get("custom_fields") or {}
        email = (
            body.get("lead_email")
            or body.get("to_email")
            or lead.get("email")
            or body.get("email")
        )
        first = lead.get("first_name") or body.get("first_name") or ""
        last = lead.get("last_name") or body.get("last_name") or ""
        name = (f"{first} {last}").strip() or (email.split("@")[0] if email else None)

        campaign_id = str(
            body.get("campaign_id") or body.get("campaignId") or custom.get("campaign_id") or ""
        ) or None
        msg_id = (
            body.get("message_id")
            or body.get("stats_id")
            or body.get("email_stats_id")
            or body.get("sl_email_lead_map_id")
        )
        ts = _parse_ts(
            body.get("event_timestamp") or body.get("timestamp") or body.get("time")
        )

        # Build a stable idempotency id even when Smartlead omits an event id.
        explicit = body.get("id") or body.get("event_id") or body.get("webhook_id")
        if explicit:
            provider_event_id = f"smartlead:{explicit}"
        else:
            basis = f"{raw_type}|{campaign_id}|{email}|{msg_id}|{ts.isoformat()}"
            provider_event_id = "smartlead:" + hashlib.sha256(basis.encode()).hexdigest()[:24]

        reply_text = None
        if etype == "replied":
            reply_text = (
                body.get("reply_message")
                or body.get("reply_body")
                or (body.get("reply") or {}).get("body")
                or body.get("message")
            )

        return [CanonicalEvent(
            type=etype,
            provider=self.name,
            provider_event_id=provider_event_id,
            occurred_at=ts,
            email=email,
            contact={
                "name": name,
                "title": lead.get("title") or custom.get("title"),
                "company": lead.get("company_name") or custom.get("company"),
            },
            campaign_id=campaign_id,
            campaign_name=body.get("campaign_name") or custom.get("campaign_name"),
            audience=(custom.get("audience") or body.get("audience") or None),
            message_ref=custom.get("message_ref") or custom.get("messageId"),
            provider_lead_id=str(lead.get("id") or body.get("lead_id") or "") or None,
            reply_text=reply_text,
            raw=body,
        )]

    async def enroll(self, message: Dict[str, Any]) -> Dict[str, Any]:
        api_key = settings.SMARTLEAD_API_KEY
        campaign_id = message.get("providerCampaignId") or settings.SMARTLEAD_DEFAULT_CAMPAIGN_ID
        if not api_key:
            raise RuntimeError("SMARTLEAD_API_KEY is not set — cannot enroll into a campaign.")
        if not campaign_id:
            raise RuntimeError(
                "No Smartlead campaign id — set SMARTLEAD_DEFAULT_CAMPAIGN_ID or pass campaignId."
            )
        url = f"{settings.SMARTLEAD_BASE_URL}/campaigns/{campaign_id}/leads"
        first, _, last = (message.get("contactName") or "").partition(" ")
        lead = {
            "email": message.get("email"),
            "first_name": first or None,
            "last_name": last or None,
            "company_name": message.get("company"),
            # Echo our ids back so webhooks map straight to this row.
            "custom_fields": {
                "message_ref": message.get("_id") or message.get("messageId"),
                "audience": message.get("audience"),
                "campaign_name": message.get("campaignName"),
            },
        }
        def _post() -> Dict[str, Any]:
            resp = requests.post(
                url, params={"api_key": api_key}, json={"lead_list": [lead]}, timeout=30
            )
            resp.raise_for_status()
            return resp.json()

        data = await asyncio.to_thread(_post)
        return {
            "providerCampaignId": str(campaign_id),
            "providerLeadId": str((data or {}).get("lead_id") or ""),
            "raw": data,
        }


# ── Cal.com (meetings only) ──────────────────────────────────────────────────
class CalcomProvider(OutreachProvider):
    name = "calcom"

    def verify_signature(self, headers: Dict[str, str], raw_body: bytes) -> bool:
        secret = settings.CALCOM_WEBHOOK_SECRET
        if not secret:
            return True
        sig = (headers.get("x-cal-signature-256") or "").strip()
        if not sig:
            return False
        digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, sig.replace("sha256=", ""))

    def normalize(self, body: Dict[str, Any]) -> List[CanonicalEvent]:
        trigger = str(body.get("triggerEvent") or "").upper()
        if trigger not in {"BOOKING_CREATED", "MEETING_STARTED", "BOOKING_RESCHEDULED"}:
            return []
        payload = body.get("payload") or {}
        attendees = payload.get("attendees") or []
        attendee = attendees[0] if attendees else {}
        email = attendee.get("email") or (payload.get("responses") or {}).get("email", {}).get("value")
        ts = _parse_ts(payload.get("startTime") or body.get("createdAt"))
        uid = payload.get("uid") or payload.get("bookingId") or payload.get("id")
        provider_event_id = "calcom:" + hashlib.sha256(
            f"{uid}|{email}|{ts.isoformat()}".encode()
        ).hexdigest()[:24]

        return [CanonicalEvent(
            type="meeting",
            provider=self.name,
            provider_event_id=provider_event_id,
            occurred_at=ts,
            email=email,
            contact={"name": attendee.get("name")},
            campaign_name=payload.get("title"),
            raw=body,
        )]

    async def enroll(self, message: Dict[str, Any]) -> Dict[str, Any]:
        raise RuntimeError("Cal.com is a meetings source, not a sender.")


# ── Factory ──────────────────────────────────────────────────────────────────
_SENDERS = {"smartlead": SmartleadProvider}
_SOURCES = {"smartlead": SmartleadProvider, "calcom": CalcomProvider}


def get_sender() -> OutreachProvider:
    """The configured outbound sending provider."""
    return _SENDERS.get((settings.OUTREACH_PROVIDER or "smartlead").lower(), SmartleadProvider)()


def get_source(name: str) -> OutreachProvider:
    """A webhook-source provider by name (smartlead | calcom)."""
    cls = _SOURCES.get((name or "").lower())
    if not cls:
        raise KeyError(f"unknown outreach source: {name}")
    return cls()
