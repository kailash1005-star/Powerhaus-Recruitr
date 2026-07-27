"""
Cost Service — the metered spend ledger behind the Cost Analyser dashboard.

Every billable third-party call (OpenAI, Gemini, Apollo, Apify, Firecrawl) is
recorded as one immutable ``cost_events`` document, tagged with the *stage* that
caused it (job_search / candidate_search / matching / outreach /
company_analysis) and the ids of the run/pipeline/job/candidate involved. Costs
are computed from a versioned ``price_book`` — never estimated in code — and the
unit price is snapshotted onto each event so history stays correct when prices
change.

Two cost kinds (per the locked plan):
  • metered      — OpenAI, Gemini, Apify, Firecrawl → true $/unit per event.
  • subscription — Apollo ($59/mo), Smartlead → flat monthly line, editable in
                   Settings. Apollo events still carry the *credits used* so the
                   flat monthly cost can be ALLOCATED across searches by usage.

Mechanics
─────────
Call sites are synchronous (OpenAI clients run in threads), so instead of an
async DB write per call we buffer events on a ``ContextVar`` set by
``cost_context(...)`` at the top of each flow, then flush the buffer once the
flow finishes. ``asyncio.to_thread`` copies the context (same buffer object), so
appends from worker threads land in the same list. The price book is cached in
memory so cost can be computed synchronously at the call site.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

from bson import ObjectId

from app.database import get_database

logger = logging.getLogger(__name__)

COST_EVENTS = "cost_events"
PRICE_BOOK = "price_book"

# ── Stages (the five spend buckets the dashboard rolls up) ──────────────────
STAGE_JOB = "job_search"
STAGE_CANDIDATE = "candidate_search"
STAGE_MATCHING = "matching"
STAGE_OUTREACH = "outreach"
STAGE_COMPANY = "company_analysis"

# ── In-memory price cache (loaded from price_book at startup) ────────────────
_METERED: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
_SUBS: Dict[str, Dict[str, Any]] = {}

# ── Per-flow event buffer ───────────────────────────────────────────────────
_sink: ContextVar[Optional[Dict[str, Any]]] = ContextVar("cost_sink", default=None)
# Events recorded with no active cost_context land here and flush opportunistically.
_ORPHANS: List[Dict[str, Any]] = []


# ──────────────────────────────────────────────────────────────────────────────
# Seed price book (real market prices, verified Jul 2026 — see cost plan)
# ──────────────────────────────────────────────────────────────────────────────

def _seed_entries() -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    common = {"effectiveFrom": now, "updatedAt": now}
    return [
        # ── metered · OpenAI ──────────────────────────────────────────────
        {"service": "openai", "model": "text-embedding-3-small", "kind": "metered",
         "unit": "token", "inUsdPer1M": 0.02, "outUsdPer1M": 0.0,
         "source": "openai.com/pricing", **common},
        {"service": "openai", "model": "gpt-4o-mini", "kind": "metered",
         "unit": "token", "inUsdPer1M": 0.15, "outUsdPer1M": 0.60,
         "source": "openai.com/pricing", **common},
        {"service": "openai", "model": "gpt-4o", "kind": "metered",
         "unit": "token", "inUsdPer1M": 2.50, "outUsdPer1M": 10.0,
         "source": "openai.com/pricing", **common},
        # ── metered · Gemini (company classifier) ─────────────────────────
        {"service": "gemini", "model": "gemini-2.5-flash", "kind": "metered",
         "unit": "token", "inUsdPer1M": 0.30, "outUsdPer1M": 2.50,
         "source": "ai.google.dev/pricing", **common},
        # ── metered · Apify (per LinkedIn profile) ────────────────────────
        {"service": "apify", "model": None, "kind": "metered",
         "unit": "profile", "usdPerUnit": 0.01,
         "source": "apify.com/harvestapi", **common},
        # ── metered · Firecrawl (per page) ────────────────────────────────
        {"service": "firecrawl", "model": None, "kind": "metered",
         "unit": "page", "usdPerUnit": 0.00083,
         "source": "firecrawl.dev/pricing", **common},
        # ── subscription · Apollo ($59/mo flat; credits attributed at a stable
        #    marginal $/credit so per-search numbers don't swing with volume) ──
        {"service": "apollo", "model": None, "kind": "subscription",
         "monthlyUsd": 59.0, "usdPerCredit": 0.20, "includedCredits": 0, "allocateBy": "credit",
         "source": "your Apollo plan", **common},
        # ── subscription · Smartlead (flat monthly; $0 until the user enters
        #    their real plan in Rates — we never fabricate a plan cost) ──────
        {"service": "smartlead", "model": None, "kind": "subscription",
         "monthlyUsd": 0.0, "allocateBy": "none", "configured": False,
         "source": "set your Smartlead plan", **common},
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Init / cache
# ──────────────────────────────────────────────────────────────────────────────

async def init_price_book() -> None:
    """Seed the price book on first run and load it into the in-memory cache.
    Safe to call on every startup (idempotent seed)."""
    db = await get_database()
    col = db[PRICE_BOOK]
    try:
        if await col.count_documents({}) == 0:
            await col.insert_many(_seed_entries())
            logger.info("[Cost] seeded price_book with %d entries", len(_seed_entries()))
        else:
            # Migrate older seeds: ensure the Apollo plan carries a marginal
            # $/credit (added after the first release) so attribution is stable.
            await col.update_one(
                {"service": "apollo", "kind": "subscription", "usdPerCredit": {"$exists": False}},
                {"$set": {"usdPerCredit": 0.20}},
            )
            # Reset the fabricated Smartlead placeholder ($94) — we don't invent
            # a plan cost. Users set their real Smartlead spend in Rates.
            await col.update_one(
                {"service": "smartlead", "kind": "subscription", "monthlyUsd": 94.0},
                {"$set": {"monthlyUsd": 0.0, "configured": False}},
            )
        await db[COST_EVENTS].create_index("createdAt", name="idx_cost_createdAt")
        await db[COST_EVENTS].create_index(
            [("stage", 1), ("createdAt", -1)], name="idx_cost_stage")
        await db[COST_EVENTS].create_index("groupKey", name="idx_cost_groupKey")
    except Exception as e:  # noqa: BLE001 — never block startup on cost setup
        logger.warning("[Cost] init_price_book warning: %s", e)
    await refresh_price_cache()


async def refresh_price_cache() -> None:
    """Reload the in-memory price cache from Mongo (call after edits)."""
    global _METERED, _SUBS
    metered: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
    subs: Dict[str, Dict[str, Any]] = {}
    try:
        db = await get_database()
        async for p in db[PRICE_BOOK].find({}):
            if p.get("kind") == "subscription":
                subs[p["service"]] = p
            else:
                metered[(p["service"], p.get("model"))] = p
    except Exception as e:  # noqa: BLE001
        logger.warning("[Cost] refresh_price_cache failed: %s", e)
        return
    _METERED, _SUBS = metered, subs
    logger.info("[Cost] price cache: %d metered, %d subscription", len(metered), len(subs))


# ──────────────────────────────────────────────────────────────────────────────
# Cost computation (synchronous — reads the in-memory cache)
# ──────────────────────────────────────────────────────────────────────────────

def _cost_of(service: str, model: Optional[str], unit: str,
             quantity: Union[int, float, Dict[str, Any]]) -> Tuple[Optional[float], float]:
    """Return (unitPriceUsd_snapshot, costUsd). Subscription services cost $0 at
    the event (their $ is the flat monthly line); their quantity is kept for
    allocation. Unknown services cost $0 but are still recorded."""
    if service in _SUBS:
        return (None, 0.0)
    entry = _METERED.get((service, model)) or _METERED.get((service, None))
    if not entry:
        return (None, 0.0)
    if entry.get("unit") == "token" or isinstance(quantity, dict):
        q = quantity if isinstance(quantity, dict) else {}
        cin = (q.get("in", 0) or 0) / 1e6 * (entry.get("inUsdPer1M", 0) or 0)
        cout = (q.get("out", 0) or 0) / 1e6 * (entry.get("outUsdPer1M", 0) or 0)
        return (None, round(cin + cout, 8))
    up = entry.get("usdPerUnit", 0.0) or 0.0
    return (up, round(up * float(quantity or 0), 8))


def _group_key(stage: Optional[str], refs: Dict[str, Any]) -> Optional[str]:
    """The id that identifies one 'line item' (one search) within a stage."""
    if not stage:
        return None
    pick = {
        STAGE_MATCHING: refs.get("matchRunId"),
        STAGE_CANDIDATE: refs.get("jobId") or refs.get("pipelineId"),
        STAGE_JOB: refs.get("runId"),
        STAGE_COMPANY: refs.get("runId") or refs.get("companyId"),
        STAGE_OUTREACH: refs.get("candidateId") or refs.get("campaignId") or "campaign",
    }.get(stage)
    return f"{stage}:{pick}" if pick else stage


# ──────────────────────────────────────────────────────────────────────────────
# Recording
# ──────────────────────────────────────────────────────────────────────────────

def record_event(
    *,
    service: str,
    operation: str,
    unit: str,
    quantity: Union[int, float, Dict[str, Any]],
    model: Optional[str] = None,
    vendor_ref: Optional[str] = None,
    cost_override: Optional[float] = None,
    stage: Optional[str] = None,
    refs: Optional[Dict[str, Any]] = None,
) -> None:
    """Record one billable call. Synchronous & exception-safe — instrumentation
    must never break the flow it measures. Buffered on the active cost_context
    and flushed when the flow ends."""
    try:
        sink = _sink.get()
        ctx_stage = stage or (sink or {}).get("stage")
        ctx_refs = {**((sink or {}).get("refs") or {}), **(refs or {})}
        if cost_override is not None:
            unit_price, cost = None, round(float(cost_override), 8)
        else:
            unit_price, cost = _cost_of(service, model, unit, quantity)
        ev = {
            "service": service,
            "operation": operation,
            "model": model,
            "stage": ctx_stage,
            "unit": unit,
            "quantity": quantity,
            "unitPriceUsd": unit_price,
            "costUsd": cost,
            "refs": ctx_refs,
            "groupKey": _group_key(ctx_stage, ctx_refs),
            "groupLabel": (sink or {}).get("label"),
            "vendorRef": vendor_ref,
            "createdAt": datetime.utcnow(),
        }
        (sink["events"] if sink is not None else _ORPHANS).append(ev)
    except Exception as e:  # noqa: BLE001
        logger.warning("[Cost] record_event dropped (%s/%s): %s", service, operation, e)


def record_chat(completion: Any, *, model: str, service: str = "openai",
                operation: str = "chat") -> None:
    """Record token cost from an OpenAI-style chat completion's ``usage``."""
    try:
        u = getattr(completion, "usage", None)
        pin = int(getattr(u, "prompt_tokens", 0) or 0)
        pout = int(getattr(u, "completion_tokens", 0) or 0)
    except Exception:  # noqa: BLE001
        pin, pout = 0, 0
    if pin or pout:
        record_event(service=service, operation=operation, model=model,
                     unit="token", quantity={"in": pin, "out": pout})


@asynccontextmanager
async def cost_context(stage: str, *, label: Optional[str] = None, **refs: Any):
    """Establish the metering context for a flow. All events recorded inside
    (including from ``asyncio.to_thread`` workers) inherit ``stage``/``refs`` and
    are flushed to Mongo on exit."""
    clean = {k: v for k, v in refs.items() if v is not None}
    token = _sink.set({"stage": stage, "label": label, "refs": clean, "events": []})
    try:
        yield
    finally:
        sink = _sink.get()
        _sink.reset(token)
        await _flush((sink or {}).get("events") or [])


async def _flush(events: List[Dict[str, Any]]) -> None:
    batch = list(events)
    if _ORPHANS:
        batch = _ORPHANS[:] + batch
        _ORPHANS.clear()
    if not batch:
        return
    try:
        db = await get_database()
        await db[COST_EVENTS].insert_many(batch, ordered=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("[Cost] flush failed (%d events): %s", len(batch), e)


# ──────────────────────────────────────────────────────────────────────────────
# Price-book read / edit (Settings → Costs)
# ──────────────────────────────────────────────────────────────────────────────

async def list_price_book() -> List[Dict[str, Any]]:
    db = await get_database()
    out: List[Dict[str, Any]] = []
    async for p in db[PRICE_BOOK].find({}).sort([("kind", 1), ("service", 1)]):
        p["_id"] = str(p["_id"])
        out.append(p)
    return out


async def update_price_entry(service: str, model: Optional[str], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Edit a price-book entry (e.g. Apollo monthlyUsd). Refreshes the cache."""
    db = await get_database()
    allowed = {"monthlyUsd", "usdPerCredit", "includedCredits", "inUsdPer1M", "outUsdPer1M", "usdPerUnit", "allocateBy", "source"}
    upd = {k: v for k, v in patch.items() if k in allowed}
    if not upd:
        raise ValueError("no editable fields provided")
    upd["updatedAt"] = datetime.utcnow()
    q: Dict[str, Any] = {"service": service}
    q["model"] = model  # match None (subscription) or a specific model
    res = await db[PRICE_BOOK].find_one_and_update(
        q, {"$set": upd}, return_document=True,
    )
    if not res:
        raise ValueError(f"price entry not found: {service}/{model}")
    await refresh_price_cache()
    res["_id"] = str(res["_id"])
    return res


# ──────────────────────────────────────────────────────────────────────────────
# Aggregation
# ──────────────────────────────────────────────────────────────────────────────

def _range_start(range_key: str) -> Optional[datetime]:
    days = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}.get(range_key)
    if days is None:
        return None  # "all"
    return datetime.utcnow() - timedelta(days=days)


async def _events_in_range(range_key: str, extra: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    db = await get_database()
    q: Dict[str, Any] = dict(extra or {})
    start = _range_start(range_key)
    if start is not None:
        q["createdAt"] = {"$gte": start}
    return [e async for e in db[COST_EVENTS].find(q)]


def _sub_monthly() -> Dict[str, float]:
    return {svc: float(p.get("monthlyUsd") or 0) for svc, p in _SUBS.items()}


def _apollo_rate() -> float:
    """Stable marginal $/credit used to ATTRIBUTE Apollo spend to searches. This
    is deliberately NOT the plan ÷ credits-used (which swings wildly on low
    volume) — it's a fixed per-credit value the user sets in Settings."""
    p = _SUBS.get("apollo") or {}
    r = p.get("usdPerCredit")
    return float(r) if r not in (None, "") else 0.20


def _event_attributed_cost(e: Dict[str, Any], apollo_rate: float) -> float:
    """The cost we ATTRIBUTE to a search for one event. Metered events use their
    real $; Apollo credits use the marginal rate (the flat plan is shown
    separately as a fixed line, so it isn't double-counted here)."""
    if e.get("service") == "apollo" and e.get("unit") == "credit":
        q = e.get("quantity")
        return (float(q) if isinstance(q, (int, float)) else 0.0) * apollo_rate
    return float(e.get("costUsd") or 0)


def _apollo_credits(events: List[Dict[str, Any]]) -> float:
    total = 0.0
    for e in events:
        if e.get("service") == "apollo" and e.get("unit") == "credit":
            q = e.get("quantity")
            total += float(q) if isinstance(q, (int, float)) else 0.0
    return total


# ── Human labels (resolved from the entity each search points at) ───────────

async def _label_for(db, stage: Optional[str], refs: Dict[str, Any], fallback: str) -> str:
    """Turn a group's refs into a readable title (job/company/run), instead of a
    raw ``stage:id`` key."""
    try:
        if stage == STAGE_MATCHING and refs.get("matchRunId"):
            d = await db["match_runs"].find_one({"_id": ObjectId(refs["matchRunId"])}, {"jdTitle": 1, "companyName": 1})
            if d:
                return " · ".join([x for x in [d.get("jdTitle"), d.get("companyName")] if x]) or fallback
        if stage == STAGE_CANDIDATE:
            title = company = None
            if refs.get("jobId"):
                j = await db["jobs"].find_one({"_id": ObjectId(refs["jobId"])}, {"title": 1})
                title = (j or {}).get("title")
            if refs.get("pipelineId"):
                p = await db["candidatePipelines"].find_one({"_id": ObjectId(refs["pipelineId"])}, {"companyName": 1})
                company = (p or {}).get("companyName")
            joined = " · ".join([x for x in [title, company] if x])
            if joined:
                return joined
        if stage == STAGE_OUTREACH and refs.get("candidateId"):
            c = await db["candidates"].find_one({"_id": ObjectId(refs["candidateId"])}, {"displayName": 1})
            if c and c.get("displayName"):
                return c["displayName"]
        if stage == STAGE_COMPANY:
            return "Company analysis"
        if stage == STAGE_JOB:
            return "Job discovery run"
    except Exception:  # noqa: BLE001
        pass
    return fallback


# ── Volume joins (pipeline counts behind unit economics) ────────────────────

def _range_days(range_key: str) -> Optional[int]:
    return {"7d": 7, "14d": 14, "30d": 30, "90d": 90}.get(range_key)


async def _global_volume(db, range_key: str) -> Dict[str, int]:
    """Pipeline throughput in the window — the denominators for unit economics."""
    cand = db["candidates"]
    start = _range_start(range_key)
    base: Dict[str, Any] = {} if start is None else {"createdAt": {"$gte": start}}
    try:
        sourced = await cand.count_documents(base)
        enriched = await cand.count_documents({**base, "isApifyEnriched": True})
        rejected = await cand.count_documents({**base, "isAccepted": False})
        # Candidates default to accepted, so "rejected" is the deliberate signal;
        # enriched-AND-rejected is the real wasted spend.
        enriched_rejected = await cand.count_documents({**base, "isApifyEnriched": True, "isAccepted": False})
        mq: Dict[str, Any] = {"source": "pipeline"}
        if start is not None:
            mq["createdAt"] = {"$gte": start}
        matches = await db["match_runs"].count_documents(mq)
    except Exception:  # noqa: BLE001
        return {"sourced": 0, "enriched": 0, "rejected": 0, "enrichedRejected": 0, "matches": 0}
    return {"sourced": sourced, "enriched": enriched, "rejected": rejected,
            "enrichedRejected": enriched_rejected, "matches": matches}


async def _group_volume(db, stage: Optional[str], refs: Dict[str, Any]) -> Dict[str, int]:
    """found → enriched → rejected for one search (or matched, for a match run)."""
    try:
        if stage == STAGE_CANDIDATE and refs.get("jobId"):
            cand = db["candidates"]
            base = {"sourceJobIds": refs["jobId"]}
            return {
                "found": await cand.count_documents(base),
                "enriched": await cand.count_documents({**base, "isApifyEnriched": True}),
                "rejected": await cand.count_documents({**base, "isAccepted": False}),
                "enrichedRejected": await cand.count_documents({**base, "isApifyEnriched": True, "isAccepted": False}),
            }
        if stage == STAGE_MATCHING and refs.get("matchRunId"):
            run = await db["match_runs"].find_one({"_id": ObjectId(refs["matchRunId"])}, {"results": 1})
            n = len((run or {}).get("results") or [])
            return {"found": n, "enriched": n, "rejected": 0, "enrichedRejected": 0}
    except Exception:  # noqa: BLE001
        pass
    return {}


async def _enrich_groups(db, groups: List[Dict[str, Any]], *, with_volume: bool = False) -> None:
    """Attach human labels (and optionally found/enriched/accepted volume)."""
    for g in groups:
        refs = g.get("refs") or {}
        g["label"] = await _label_for(db, g.get("stage"), refs, g.get("label") or g["groupKey"])
        if with_volume:
            vol = await _group_volume(db, g.get("stage"), refs)
            g["found"] = vol.get("found")
            g["enriched"] = vol.get("enriched")
            g["rejected"] = vol.get("rejected")
            g["enrichedRejected"] = vol.get("enrichedRejected")
            g["perEnriched"] = round(g["cost"] / vol["enriched"], 4) if vol.get("enriched") else None


def _group_events(events: List[Dict[str, Any]], apollo_rate: float) -> List[Dict[str, Any]]:
    """Collapse events into line items (one per groupKey) with a service breakdown
    and the attributed (metered + Apollo-marginal) cost."""
    groups: Dict[str, Dict[str, Any]] = {}
    for e in events:
        gk = e.get("groupKey")
        if not gk:
            continue
        g = groups.setdefault(gk, {
            "groupKey": gk, "stage": e.get("stage"), "label": e.get("groupLabel"),
            "cost": 0.0, "byService": {}, "credits": 0.0, "refs": e.get("refs") or {},
        })
        if e.get("groupLabel") and not g["label"]:
            g["label"] = e["groupLabel"]
        c = _event_attributed_cost(e, apollo_rate)
        if e.get("service") == "apollo" and e.get("unit") == "credit" and isinstance(e.get("quantity"), (int, float)):
            g["credits"] += float(e["quantity"])
        g["cost"] += c
        g["byService"][e.get("service")] = g["byService"].get(e.get("service"), 0.0) + c
    out = []
    for g in groups.values():
        g["cost"] = round(g["cost"], 4)
        g["byService"] = [{"service": k, "cost": round(v, 4)} for k, v in
                          sorted(g["byService"].items(), key=lambda kv: kv[1], reverse=True)]
        out.append(g)
    return out


async def _events_between(start: datetime, end: datetime) -> List[Dict[str, Any]]:
    db = await get_database()
    return [e async for e in db[COST_EVENTS].find({"createdAt": {"$gte": start, "$lt": end}})]


def _operational(events: List[Dict[str, Any]], rate: float) -> float:
    """Variable spend you can influence: metered + Apollo credits @ marginal rate."""
    return sum(_event_attributed_cost(e, rate) for e in events)


def _apollo_allowance() -> int:
    p = _SUBS.get("apollo") or {}
    try:
        return int(p.get("includedCredits") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _build_insights(*, rate: float, operational: float, prev: Optional[float],
                    vol: Dict[str, int], credits: float, allowance: int,
                    top: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic, threshold-gated optimisation hints — silent on an idle
    account (each rule renders only when it actually applies)."""
    out: List[Dict[str, Any]] = []
    # Real waste = candidates you paid to enrich that were then rejected.
    wasted = vol.get("enrichedRejected", 0)
    if wasted >= 5:
        out.append({
            "type": "over_enrichment", "severity": "warn", "title": "Wasted enrichment",
            "body": f"{wasted} enriched candidate(s) were later rejected — enrichment you didn't need "
                    f"(≈ ${wasted * rate:.2f} of Apollo credits). Enrich after accept to reclaim it.",
        })
    if allowance > 0 and credits / allowance < 0.4:
        out.append({
            "type": "apollo_underuse", "severity": "info", "title": "Apollo plan underused",
            "body": f"Used {int(credits)} of {allowance} credits ({credits / allowance * 100:.0f}%). "
                    f"A smaller plan — or more sourcing — improves your $/credit.",
        })
    if top:
        t = top[0]
        out.append({
            "type": "priciest", "severity": "info", "title": "Priciest activity",
            "body": f"“{t.get('label')}” spent ${t.get('cost', 0):.2f} — your most expensive line item.",
        })
    if prev and prev > 0 and operational > prev * 1.25:
        out.append({
            "type": "run_rate", "severity": "warn", "title": "Spend rising",
            "body": f"Operational spend is up {(operational - prev) / prev * 100:.0f}% vs the previous period.",
        })
    return out


async def overview(range_key: str = "30d") -> Dict[str, Any]:
    """Operational-first overview: variable spend + run-rate, unit economics,
    by-stage/service, subscription utilisation, and optimisation insights.
    Subscriptions are the flat bill; operational is what you can influence."""
    db = await get_database()
    events = await _events_in_range(range_key)
    subs = _sub_monthly()
    rate = _apollo_rate()

    metered_total = sum(float(e.get("costUsd") or 0) for e in events)
    subs_total = sum(subs.values())
    credits = _apollo_credits(events)
    operational = round(_operational(events, rate), 4)

    # Prior-period operational (for the delta) + run-rate projection.
    days = _range_days(range_key)
    prev_op: Optional[float] = None
    run_rate: Optional[float] = None
    if days:
        now = datetime.utcnow()
        prev_events = await _events_between(now - timedelta(days=days * 2), now - timedelta(days=days))
        prev_op = round(_operational(prev_events, rate), 4)
        run_rate = round(operational / days * 30, 2)

    # Bill composition by service (metered $ + each subscription flat).
    by_service: Dict[str, float] = {}
    for e in events:
        s = e.get("service")
        if s in _SUBS:
            continue
        by_service[s] = by_service.get(s, 0.0) + float(e.get("costUsd") or 0)
    for s, amt in subs.items():
        by_service[s] = by_service.get(s, 0.0) + amt

    # Variable by stage (+ how many searches/runs each stage holds).
    stage_cost: Dict[str, float] = {}
    stage_keys: Dict[str, set] = {}
    for e in events:
        st = e.get("stage") or "other"
        stage_cost[st] = stage_cost.get(st, 0.0) + _event_attributed_cost(e, rate)
        if e.get("groupKey"):
            stage_keys.setdefault(st, set()).add(e["groupKey"])

    # Daily trend.
    trend: Dict[str, float] = {}
    for e in events:
        d = e.get("createdAt")
        key = (d.date().isoformat() if isinstance(d, datetime) else "?")
        trend[key] = trend.get(key, 0.0) + _event_attributed_cost(e, rate)
    daily = [{"date": k, "cost": round(v, 4)} for k, v in sorted(trend.items()) if k != "?"]

    # Top activity with efficiency (found/enriched/accepted, $/enriched).
    groups = sorted(_group_events(events, rate), key=lambda g: g["cost"], reverse=True)
    top = groups[:8]
    await _enrich_groups(db, top, with_volume=True)

    vol = await _global_volume(db, range_key)
    allowance = _apollo_allowance()

    def per(n: int) -> Optional[float]:
        return round(operational / n, 4) if n else None

    insights = _build_insights(rate=rate, operational=operational, prev=prev_op,
                               vol=vol, credits=credits, allowance=allowance, top=top)

    return {
        "range": range_key,
        "operational": operational,
        "operationalPrev": prev_op,
        "deltaPct": (round((operational - prev_op) / prev_op * 100, 1) if prev_op else None),
        "runRate": run_rate,
        "fixedMonthly": round(subs_total, 2),
        "billTotal": round(metered_total + subs_total, 2),
        "apolloRate": rate,
        "unitEconomics": {
            **vol,
            "perSourced": per(vol["sourced"]),
            "perEnriched": per(vol["enriched"]),
            "perMatch": per(vol["matches"]),
            "wastedEnrichmentUsd": round((vol.get("enrichedRejected") or 0) * rate, 2),
        },
        "subscriptions": [
            {"service": s, "monthlyUsd": round(v, 2),
             "creditsUsed": round(credits, 0) if s == "apollo" else None,
             "includedCredits": (allowance or None) if s == "apollo" else None,
             "usdPerCredit": rate if s == "apollo" else None,
             "utilizationPct": (round(credits / allowance * 100, 1) if (s == "apollo" and allowance) else None),
             "configured": bool(v > 0)}
            for s, v in subs.items()
        ],
        "byService": [{"service": k, "cost": round(v, 2), "fixed": k in _SUBS} for k, v in
                      sorted(by_service.items(), key=lambda kv: kv[1], reverse=True)],
        "byStage": [{"stage": k, "cost": round(v, 4), "count": len(stage_keys.get(k, set()))}
                    for k, v in sorted(stage_cost.items(), key=lambda kv: kv[1], reverse=True) if v > 0],
        "daily": daily,
        "insights": insights,
        "topSearches": top,
    }


async def group(stage: str, range_key: str = "30d") -> Dict[str, Any]:
    """All line items in a stage, with sourcing→enrich→accept efficiency."""
    db = await get_database()
    events = await _events_in_range(range_key, {"stage": stage})
    rate = _apollo_rate()
    credits = _apollo_credits(events)
    operational = round(_operational(events, rate), 4)
    groups = sorted(_group_events(events, rate), key=lambda g: g["cost"], reverse=True)
    await _enrich_groups(db, groups, with_volume=True)

    enriched = sum(g.get("enriched") or 0 for g in groups)
    wasted = sum(g.get("enrichedRejected") or 0 for g in groups)
    insights = _build_insights(rate=rate, operational=operational, prev=None,
                               vol={"enrichedRejected": wasted},
                               credits=credits, allowance=0, top=groups[:1])
    return {
        "stage": stage,
        "range": range_key,
        "total": operational,
        "count": len(groups),
        "creditsUsed": round(credits, 0),
        "apolloRate": rate,
        "enriched": enriched,
        "enrichedRejected": wasted,
        "perEnriched": round(operational / enriched, 4) if enriched else None,
        "insights": insights,
        "items": groups,
    }


async def line_item(group_key: str) -> Dict[str, Any]:
    """One search: itemized events, breakdown, volume, and an item insight."""
    db = await get_database()
    events = [e async for e in db[COST_EVENTS].find({"groupKey": group_key})]
    rate = _apollo_rate()

    rows: List[Dict[str, Any]] = []
    by_service: Dict[str, float] = {}
    total = 0.0
    label = None
    stage = None
    refs: Dict[str, Any] = {}
    for e in events:
        label = label or e.get("groupLabel")
        stage = stage or e.get("stage")
        refs = refs or (e.get("refs") or {})
        cost = _event_attributed_cost(e, rate)
        allocated = e.get("service") == "apollo" and e.get("unit") == "credit"
        rows.append({
            "service": e.get("service"), "operation": e.get("operation"),
            "model": e.get("model"), "unit": e.get("unit"),
            "quantity": e.get("quantity"),
            "unitPriceUsd": rate if allocated else e.get("unitPriceUsd"),
            "costUsd": round(cost, 6),
            "allocated": bool(allocated),
            "createdAt": e.get("createdAt").isoformat() + "Z" if isinstance(e.get("createdAt"), datetime) else None,
        })
        by_service[e.get("service")] = by_service.get(e.get("service"), 0.0) + cost
        total += cost
    # Show the activity in the order it happened.
    rows.sort(key=lambda r: r.get("createdAt") or "")
    resolved = await _label_for(db, stage, refs, label or group_key)
    vol = await _group_volume(db, stage, refs)
    total = round(total, 4)

    insights = _build_insights(
        rate=rate, operational=total, prev=None,
        vol=vol, credits=0, allowance=0, top=[],
    )
    return {
        "groupKey": group_key,
        "stage": stage,
        "label": resolved,
        "total": total,
        "apolloRate": rate,
        "found": vol.get("found"),
        "enriched": vol.get("enriched"),
        "rejected": vol.get("rejected"),
        "enrichedRejected": vol.get("enrichedRejected"),
        "refs": refs,
        "byService": [{"service": k, "cost": round(v, 4)} for k, v in
                      sorted(by_service.items(), key=lambda kv: kv[1], reverse=True)],
        "events": rows,
        "insights": insights,
    }
