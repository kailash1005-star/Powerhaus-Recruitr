"""
Outreach CRM document models.

Two collections power the Outreach → Leads / Candidates CRM:

  • `outreach_messages` — the READ MODEL the UI renders. One doc per
    (recipient, campaign). Carries the contact, linkage back to the source
    record (HR lead / cv_candidate), provider ids, and a DERIVED funnel status.

  • `outreach_events` — an append-only AUDIT LOG. One doc per provider event
    (sent / opened / replied / bounced / unsubscribed / meeting). `providerEventId`
    is unique so webhook + reconcile paths are idempotent, and the message status
    can always be recomputed from the log.

Status is never overwritten blindly — it is derived from a funnel precedence so a
later "open" can't downgrade an earlier "reply" (see STAGE_RANK). `bounced` and
`unsubscribed` are terminal flags that take over the displayed status.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from bson import ObjectId
from pydantic import BaseModel, Field

# Funnel precedence. Higher = further down the funnel; status only moves forward.
STAGE_RANK: Dict[str, int] = {
    "sent": 1,
    "delivered": 2,
    "opened": 3,
    "clicked": 4,
    "replied": 5,
    "meeting": 6,
}

# Terminal override flags (displayed as the status regardless of funnel stage).
TERMINAL_TYPES = {"bounced", "unsubscribed"}

# Everything the ingestion layer will accept from a provider adapter.
VALID_EVENT_TYPES = set(STAGE_RANK) | TERMINAL_TYPES

# Human label shown in the CRM's "last activity" column, per event type.
ACTIVITY_LABEL = {
    "sent": "Delivered",
    "delivered": "Delivered",
    "opened": "Opened",
    "clicked": "Clicked a link",
    "replied": "Replied",
    "meeting": "Meeting booked",
    "bounced": "Bounced",
    "unsubscribed": "Unsubscribed",
}


class OutreachFlags(BaseModel):
    bounced: bool = False
    unsubscribed: bool = False


class OutreachMessageModel(BaseModel):
    """`outreach_messages` document — the CRM read model."""
    id: Optional[str] = Field(None, alias="_id")

    tenantId: str = "default"
    audience: str = "lead"               # "lead" (HR) | "candidate"
    dedupeKey: str                       # stable natural id (provider:campaign:email)

    # Contact
    contactName: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None          # job title / current role
    company: Optional[str] = None        # for leads
    roleTitle: Optional[str] = None      # role matched, for candidates

    channel: str = "Email"               # "Email" | "LinkedIn"

    # Linkage back to the source records
    leadId: Optional[str] = None         # prospects._id (HR decision-maker)
    candidateId: Optional[str] = None    # cv_candidates._id

    # Provider linkage
    provider: Optional[str] = None
    providerCampaignId: Optional[str] = None
    providerLeadId: Optional[str] = None
    campaignName: Optional[str] = None   # role / client mandate this belongs to

    # Derived funnel state
    status: str = "sent"
    stageRank: int = 0
    flags: OutreachFlags = Field(default_factory=OutreachFlags)
    replyClass: Optional[str] = None     # interested | not_interested | ooo | opt_out

    lastActivity: Optional[str] = None   # label for the latest event
    lastActivityAt: Optional[datetime] = None
    sentAt: Optional[datetime] = None

    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class OutreachEventModel(BaseModel):
    """`outreach_events` document — append-only audit log."""
    id: Optional[str] = Field(None, alias="_id")

    tenantId: str = "default"
    messageId: Optional[str] = None
    type: str                            # one of VALID_EVENT_TYPES
    provider: str                        # "smartlead" | "calcom"
    providerEventId: str                 # unique → idempotency
    occurredAt: Optional[datetime] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    createdAt: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
