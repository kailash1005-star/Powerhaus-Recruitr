"""
Apollo candidate enrichment (stage 1 of the two-stage enrich).

Calls Apollo ``/people/match`` for one candidate to obtain the AUTHORITATIVE
LinkedIn URL, verified email, and real name/location — the inputs the Apify
profile scraper (stage 2) needs. Idempotent: a candidate already carrying
``isEnriched`` is returned unchanged.

Storage policy mirrors the original inline endpoint:
  • ``enrichedData``  — UI-friendly projection of the fields Apollo returns.
  • ``enrichedRaw``   — the full untouched ``/people/match`` envelope (audit).
  • top-level hydration of firstName/lastName/displayName/location/currentTitle/
    externalLinkedinUrl/currentCompany so the table shows the real data.

This is shared by the single-candidate endpoint and the bulk background job.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from app.config import APOLLO_BASE_URL, settings

logger = logging.getLogger(__name__)


class ApolloEnrichError(Exception):
    """Apollo /people/match failed or returned no person."""


def _people_match(apollo_id: str) -> Optional[Dict[str, Any]]:
    """Blocking Apollo /people/match call — returns the full envelope or None."""
    resp = requests.post(
        f"{APOLLO_BASE_URL}/people/match",
        headers={
            "x-api-key": settings.APOLLO_API_KEY,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        },
        json={"id": apollo_id, "reveal_personal_emails": True},
        timeout=30,
    )
    resp.raise_for_status()
    # Apollo /people/match consumes 1 credit (email). Apollo is a flat monthly
    # subscription, so cost is $0 at the event — the credit is recorded for the
    # dashboard to allocate the plan across searches by usage.
    try:
        from app.services import cost_service
        cost_service.record_event(
            service="apollo", operation="people_match",
            unit="credit", quantity=1, vendor_ref=str(apollo_id),
        )
    except Exception:  # noqa: BLE001
        pass
    return resp.json()


def _project(raw_envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Build the {top_level_updates} dict to $set on the candidate doc."""
    person = raw_envelope["person"]
    org = person.get("organization") or {}
    now = datetime.utcnow()

    emp_history_trimmed = [
        {
            "title": e.get("title"),
            "organizationName": e.get("organization_name"),
            "organizationId": e.get("organization_id"),
            "startDate": e.get("start_date"),
            "endDate": e.get("end_date"),
            "current": bool(e.get("current")),
        }
        for e in (person.get("employment_history") or [])
    ]
    org_slim = {
        "name": org.get("name"),
        "primaryDomain": org.get("primary_domain"),
        "industry": org.get("industry"),
        "estimatedNumEmployees": org.get("estimated_num_employees"),
        "foundedYear": org.get("founded_year"),
        "hqCity": org.get("city"),
        "hqCountry": org.get("country"),
        "shortDescription": org.get("short_description"),
        "logoUrl": org.get("logo_url"),
        "linkedinUrl": org.get("linkedin_url"),
        "websiteUrl": org.get("website_url"),
    }
    enriched_data = {
        "email": person.get("email"),
        "emailStatus": person.get("email_status"),
        "personalEmails": person.get("personal_emails") or [],
        "linkedinUrl": person.get("linkedin_url"),
        "photoUrl": person.get("photo_url"),
        "title": person.get("title"),
        "headline": person.get("headline"),
        "seniority": person.get("seniority"),
        "functions": person.get("functions") or [],
        "departments": person.get("departments") or [],
        "location": person.get("formatted_address"),
        "timeZone": person.get("time_zone"),
        "employmentHistory": emp_history_trimmed,
        "socials": {
            "twitter": person.get("twitter_url"),
            "github": person.get("github_url"),
            "facebook": person.get("facebook_url"),
        },
        "organization": org_slim,
    }

    top_level: Dict[str, Any] = {
        "isEnriched": True,
        "enrichedAt": now,
        "enrichedData": enriched_data,
        "enrichedRaw": raw_envelope,
        "enrichedSource": "apollo:/people/match",
        "updatedAt": now,
    }
    if person.get("first_name"):
        top_level["firstName"] = person["first_name"]
    if person.get("last_name"):
        top_level["lastName"] = person["last_name"]
    if person.get("name"):
        top_level["displayName"] = person["name"]
    if person.get("formatted_address"):
        top_level["location"] = person["formatted_address"]
    if person.get("title"):
        top_level["currentTitle"] = person["title"]
    if person.get("headline"):
        top_level["headline"] = person["headline"]
    if person.get("linkedin_url"):
        top_level["externalLinkedinUrl"] = person["linkedin_url"]
    if org.get("name"):
        top_level["currentCompany"] = org["name"]
    if org.get("primary_domain"):
        top_level["currentCompanyDomain"] = org["primary_domain"]
    return top_level


async def apollo_enrich_candidate(db, candidate_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich one candidate via Apollo /people/match (idempotent).

    Returns the fresh candidate doc. Raises ``ApolloEnrichError`` if the person
    has no ``apolloId`` or Apollo returns no data.
    """
    col = db["candidates"]
    oid = candidate_doc["_id"]

    if candidate_doc.get("isEnriched"):
        return candidate_doc  # already Apollo-enriched — skip (idempotent)

    apollo_id = candidate_doc.get("apolloId")
    if not apollo_id:
        raise ApolloEnrichError("candidate has no apolloId")

    try:
        raw_envelope = await asyncio.to_thread(_people_match, apollo_id)
    except Exception as exc:  # network / HTTP error
        raise ApolloEnrichError(f"Apollo /people/match failed: {exc}") from exc

    if not raw_envelope or not raw_envelope.get("person"):
        raise ApolloEnrichError("Apollo enrichment returned no data")

    await col.update_one({"_id": oid}, {"$set": _project(raw_envelope)})
    fresh = await col.find_one({"_id": oid})
    logger.info("[ApolloEnrich] enriched candidate %s", oid)
    return fresh
