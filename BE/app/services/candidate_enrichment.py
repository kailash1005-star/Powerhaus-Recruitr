"""
Candidate enrichment orchestrator — the on-demand "make candidates match-ready"
step.

Given a set of Apollo-sourced ``candidates`` docs (by id, or by pipeline/job),
this:

  1. collects their ``externalLinkedinUrl``s (skips already-enriched unless
     ``force``),
  2. serves any within-TTL profiles from the ``profileEnrichmentCache`` (so we
     never re-pay Apify for the same profile),
  3. runs ONE Apify actor call for the rest (blocking → offloaded to a thread),
  4. merges each Apify profile with the candidate's stored Apollo fields
     (``candidate_merge.merge_enriched``),
  5. writes ``enrichedData`` + ``isEnriched``/``enrichedAt`` back onto the
     candidate doc (the slots ``candidate_pipeline`` already reserves).

Returns a summary: ``{"enriched": n, "cached": n, "not_found": n, "skipped": n,
"failed": n}``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.config import settings
from app.database import get_collection
from app.services import candidate_merge
from app.services.apify_profile_service import (
    ApifyCostGuard,
    ApifyEnrichmentError,
    get_apify_profile_service,
    normalize_identifier,
)

logger = logging.getLogger(__name__)

_CACHE_COLLECTION = "profileEnrichmentCache"


# ──────────────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────────────

async def _cache_lookup(identifiers: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return {identifier: apify_profile} for identifiers cached within TTL."""
    if not identifiers:
        return {}
    cache = await get_collection(_CACHE_COLLECTION)
    cutoff = datetime.utcnow() - timedelta(days=settings.PROFILE_CACHE_TTL_DAYS)
    out: Dict[str, Dict[str, Any]] = {}
    async for doc in cache.find(
        {"_id": {"$in": identifiers}, "fetchedAt": {"$gte": cutoff}}
    ):
        if doc.get("profile"):
            out[doc["_id"]] = doc["profile"]
    return out


async def _cache_store(profiles: Dict[str, Dict[str, Any]]) -> None:
    """Upsert freshly-fetched profiles into the cache."""
    if not profiles:
        return
    cache = await get_collection(_CACHE_COLLECTION)
    now = datetime.utcnow()
    for ident, profile in profiles.items():
        try:
            await cache.update_one(
                {"_id": ident},
                {"$set": {"profile": profile, "fetchedAt": now}},
                upsert=True,
            )
        except Exception as e:  # noqa: BLE001 — cache write must never fail enrichment
            logger.warning("[Enrich] cache upsert failed for %s: %s", ident, e)


# ──────────────────────────────────────────────────────────────────────────────
# Apollo-min reconstruction (candidate docs store a subset of the Apollo person)
# ──────────────────────────────────────────────────────────────────────────────

def _apollo_person_for_merge(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Build the Apollo-person shape ``merge_enriched`` expects from a candidate.

    When the Apollo `/people/match` stage has already run, its projection
    (``enrichedData``) carries the verified email + status — use it. Otherwise
    fall back to the sparse fields the free search persisted on the candidate.
    """
    enriched = doc.get("enrichedData") or {}
    return {
        "id": doc.get("apolloId"),
        "first_name": doc.get("firstName"),
        "last_name": doc.get("lastName"),
        "name": doc.get("displayName"),
        "title": doc.get("currentTitle"),
        "headline": doc.get("headline"),
        "linkedin_url": doc.get("externalLinkedinUrl") or enriched.get("linkedinUrl"),
        "email": enriched.get("email"),
        "email_status": enriched.get("emailStatus"),
        "organization": {
            "name": doc.get("currentCompany"),
            "primary_domain": doc.get("currentCompanyDomain"),
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Public
# ──────────────────────────────────────────────────────────────────────────────

async def enrich_candidates(
    *,
    candidate_ids: Optional[List[str]] = None,
    pipeline_id: Optional[str] = None,
    job_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Enrich a selected set of candidates on demand.

    Selection is EITHER an explicit ``candidate_ids`` list OR a
    ``pipeline_id`` (optionally narrowed by ``job_id``). ``force`` re-enriches
    even already-enriched candidates (and bypasses the cache read).
    """
    candidates_col = await get_collection("candidates")

    # ── Build selection query ───────────────────────────────────────────────
    query: Dict[str, Any] = {}
    if candidate_ids:
        try:
            query["_id"] = {"$in": [ObjectId(cid) for cid in candidate_ids]}
        except Exception as exc:
            raise ValueError(f"invalid candidate id in {candidate_ids}: {exc}") from exc
    elif pipeline_id:
        query["pipelineId"] = pipeline_id
        if job_id:
            query["sourceJobIds"] = job_id
    else:
        raise ValueError("provide candidate_ids or pipeline_id")

    docs: List[Dict[str, Any]] = [d async for d in candidates_col.find(query)]
    summary = {"selected": len(docs), "enriched": 0, "cached": 0,
               "not_found": 0, "skipped": 0, "failed": 0}
    if not docs:
        return summary

    # ── Decide who needs work ───────────────────────────────────────────────
    # Map normalized identifier → list of candidate docs sharing it.
    ident_to_docs: Dict[str, List[Dict[str, Any]]] = {}
    for doc in docs:
        if doc.get("isApifyEnriched") and not force:
            summary["skipped"] += 1
            continue
        ident = normalize_identifier(doc.get("externalLinkedinUrl") or "")
        if not ident:
            summary["skipped"] += 1
            continue
        ident_to_docs.setdefault(ident, []).append(doc)

    if not ident_to_docs:
        return summary

    identifiers = list(ident_to_docs.keys())

    # ── Cache read (unless force) ───────────────────────────────────────────
    profiles: Dict[str, Dict[str, Any]] = {}
    if not force:
        profiles = await _cache_lookup(identifiers)
        summary["cached"] = len(profiles)

    to_fetch = [i for i in identifiers if i not in profiles]

    # ── Fetch the rest from Apify (blocking → thread) ───────────────────────
    if to_fetch:
        service = get_apify_profile_service()
        try:
            fetched = await asyncio.to_thread(service.enrich_profiles, to_fetch)
        except ApifyCostGuard:
            raise  # surface to the endpoint as a 400 (batch too big)
        except ApifyEnrichmentError as exc:
            logger.error("[Enrich] Apify enrichment failed: %s", exc)
            raise
        await _cache_store(fetched)
        profiles.update(fetched)

    # ── Merge + persist per candidate ───────────────────────────────────────
    now = datetime.utcnow()
    for ident, doc_list in ident_to_docs.items():
        profile = profiles.get(ident)
        for doc in doc_list:
            if not profile:
                summary["not_found"] += 1
                await candidates_col.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"apifyEnrichmentStatus": "not_found", "updatedAt": now}},
                )
                continue
            # Merge with the candidate's Apollo record when present. Prefer the
            # full Apollo /people/match projection (enrichedData) if the Apollo
            # stage already ran; else the minimal fields on the candidate doc.
            apollo_src = _apollo_person_for_merge(doc)
            enriched = candidate_merge.merge_enriched(apollo_src, profile)
            await candidates_col.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "isApifyEnriched": True,
                    "apifyEnrichedAt": now,
                    "apifyEnrichment": enriched,
                    "apifyEnrichmentStatus": "enriched",
                    "updatedAt": now,
                }},
            )
            summary["enriched"] += 1

    logger.info("[Enrich] done: %s", summary)
    return summary


# ──────────────────────────────────────────────────────────────────────────────
# Two-stage bulk enrich: Apollo /people/match → Apify deep profile
# ──────────────────────────────────────────────────────────────────────────────

async def bulk_enrich(
    *,
    pipeline_id: Optional[str] = None,
    job_id: Optional[str] = None,
    candidate_ids: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Enrich a selected set of candidates through BOTH stages, idempotently.

    Stage 1 (Apollo /people/match) yields the authoritative LinkedIn URL +
    verified email + real name; stage 2 (Apify) extracts the deep profile from
    that URL. Each stage is skipped when already done ("only un-enriched").

    Returns ``{selected, apollo_enriched, apify_enriched, cached, not_found,
    apollo_failed, skipped}``.
    """
    from app.database import get_database
    from app.services.apollo_enrich import ApolloEnrichError, apollo_enrich_candidate

    db = await get_database()
    candidates_col = db["candidates"]

    # Resolve the target candidate ids (same selection logic as enrich_candidates).
    query: Dict[str, Any] = {}
    if candidate_ids:
        query["_id"] = {"$in": [ObjectId(cid) for cid in candidate_ids]}
    elif pipeline_id:
        query["pipelineId"] = pipeline_id
        if job_id:
            query["sourceJobIds"] = job_id
    else:
        raise ValueError("provide candidate_ids or pipeline_id")

    docs: List[Dict[str, Any]] = [d async for d in candidates_col.find(query)]
    ids = [str(d["_id"]) for d in docs]
    result: Dict[str, Any] = {
        "selected": len(docs), "apollo_enriched": 0, "apollo_failed": 0,
    }
    if not docs:
        result.update({"apify_enriched": 0, "cached": 0, "not_found": 0, "skipped": 0})
        return result

    # ── Stage 1: Apollo /people/match (idempotent per candidate) ────────────
    for doc in docs:
        if doc.get("isEnriched") and not force:
            continue  # already Apollo-enriched
        try:
            await apollo_enrich_candidate(db, doc)
        except ApolloEnrichError as e:
            logger.warning("[BulkEnrich] Apollo stage failed for %s: %s", doc["_id"], e)
            result["apollo_failed"] += 1
            continue
        result["apollo_enriched"] += 1

    # ── Stage 2: Apify deep profile (uses the now-authoritative LinkedIn URL) ─
    apify_summary = await enrich_candidates(candidate_ids=ids, force=force)
    result.update({
        "apify_enriched": apify_summary["enriched"],
        "cached": apify_summary["cached"],
        "not_found": apify_summary["not_found"],
        "skipped": apify_summary["skipped"],
    })
    logger.info("[BulkEnrich] done: %s", result)
    return result
