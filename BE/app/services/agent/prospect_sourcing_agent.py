"""Pydantic-AI agent that sources the right decision-makers for a company.

Replaces the old static HR-title + keyword accept/reject prospect search. Given
the role(s) a company is hiring for, the agent reasons about which senior
decision-maker ("functional head") OWNS that function, searches Apollo for that
title at the company, JUDGES whether the returned people actually match, and
retries with alternative titles until it finds a good set — then returns the
people to store. There are no keyword filter lists; the LLM decides relevance.

Provider-swappable via settings.AGENT_MODEL (same convention as agent_factory).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.config import settings
from app.services.agent.agent_factory import _ensure_provider_env
from app.services.apollo_service import ApolloService

logger = logging.getLogger(__name__)

# Safety caps — the agent is autonomous but must stay bounded (Apollo rate limit
# + LLM cost). At most this many distinct searches and stored prospects per company.
MAX_SEARCHES = 6
MAX_PEOPLE = 25


@dataclass
class SourcingDeps:
    domain: str
    company_name: str
    industry: str
    job_titles: list[str]
    apollo: ApolloService
    found: dict[int, dict] = field(default_factory=dict)   # search_id -> {titles, people}
    _counter: dict = field(default_factory=lambda: {"n": 0})


class SourcingResult(BaseModel):
    decision_maker_titles: list[str] = Field(
        default_factory=list,
        description="The functional-head titles you decided to contact.",
    )
    accepted_search_ids: list[int] = Field(
        default_factory=list,
        description="Title-search search_id(s) whose ENTIRE people list should be stored.",
    )
    accepted_person_refs: list[str] = Field(
        default_factory=list,
        description="Specific person refs (e.g. '4.2') to store — used to hand-pick "
                    "decision-makers from a plain company roster.",
    )
    reasoning: str = Field("", description="One line: which function(s)/people you targeted and why.")
    confident: bool = Field(
        False, description="True if the accepted people clearly match the intended decision-makers.",
    )


INSTRUCTIONS = """You source the right DECISION-MAKERS to contact at a company for
a recruitment outreach. The company is hiring for one or more roles; identify the
senior person who OWNS the function each open role belongs to — the "functional
head" who would be the buyer for recruitment help.

Map the role's function to its leadership (generalise, do not hardcode):
- Marketing / Growth / Demand-gen → CMO, VP Marketing, Head of Marketing
- Engineering / Developer / DevOps → CTO, VP Engineering, Head of Engineering
- Sales / Revenue / Business Dev   → CRO, VP Sales, Head of Sales
- Product / Design                 → CPO, VP Product, Head of Product
- Finance / Accounting             → CFO, VP Finance, Head of Finance
- People / HR / Talent             → CHRO, VP People, Head of Talent
- Operations / Supply chain        → COO, VP Operations, Head of Operations
If the open role is itself a Head/C-level (e.g. "Head of Product Design"), target
the level ABOVE it or its closest peer leader (e.g. CPO / VP Product).

Process — be efficient (a few searches at most):
1. From the job title(s), pick 2-4 candidate decision-maker titles for the
   relevant function(s).
2. Call `search_company_people(target_titles, rationale)`. Read the returned
   `result_count` and `sample_titles`.
3. JUDGE: do those people's titles actually represent that function's leadership
   at this company? If yes and result_count > 0, accept that search_id.
4. If result_count == 0 OR the titles look wrong, call `search_company_people`
   again with broader/alternative titles (e.g. add the C-level, or relax to
   "Head of <function>"). NEVER repeat the same titles.
5. FALLBACK — if after 2-3 title searches you STILL have no usable people (common
   for small startups whose titles don't match standard leadership labels), call
   `list_company_roster` ONCE. It returns the company's whole people list with a
   `ref` per person. Hand-pick the real decision-makers from it — founders,
   co-founders, C-level, owners, and the senior-most person in the relevant
   function (e.g. "Senior AI Engineer" or "Engineering Lead" at a tiny startup).
   Return their refs in `accepted_person_refs`. Avoid interns / clearly junior
   people unless nobody senior exists.
6. Stop once you have good people, or after a few attempts. If a tool returns a
   "notice", stop and finalise immediately.

Return SourcingResult:
- `accepted_search_ids` — title searches whose ENTIRE result to store (use when
  the targeted titles matched well).
- `accepted_person_refs` — specific people you hand-picked from
  `list_company_roster` (the plain-search fallback).
- `decision_maker_titles`, a one-line `reasoning`, and `confident`.
Prefer fewer, clearly-relevant people. Only accept people that were actually
returned by a tool — never invent refs."""


def _build_agent() -> Agent:
    _ensure_provider_env(settings.AGENT_MODEL)
    return Agent(
        settings.AGENT_MODEL,
        output_type=SourcingResult,
        deps_type=SourcingDeps,
        instructions=INSTRUCTIONS,
        retries=2,
    )


agent = _build_agent()


@agent.tool
async def search_company_people(
    ctx: RunContext[SourcingDeps], target_titles: list[str], rationale: str = "",
) -> dict[str, Any]:
    """Search Apollo for people at the target company matching `target_titles`
    (similar titles included, any department). Returns a small summary — the full
    people list is retained server-side and stored only if you accept the search."""
    counter = ctx.deps._counter
    if counter["n"] >= MAX_SEARCHES:
        return {"notice": "Search budget exhausted — finalise now with the results you already have."}
    if not target_titles:
        return {"notice": "Provide at least one target title."}

    counter["n"] += 1
    sid = counter["n"]
    people = await asyncio.to_thread(
        ctx.deps.apollo.search_people_by_titles, ctx.deps.domain, target_titles,
    )
    ctx.deps.found[sid] = {"titles": list(target_titles), "people": people}
    sample = [
        {"name": (p.get("first_name") or p.get("name") or "").strip(), "title": p.get("title") or ""}
        for p in people[:8]
    ]
    return {
        "search_id": sid,
        "queried_titles": list(target_titles),
        "result_count": len(people),
        "sample_titles": sample,
    }


@agent.tool
async def list_company_roster(ctx: RunContext[SourcingDeps]) -> dict[str, Any]:
    """FALLBACK: list the company's whole people roster (plain domain search, no
    title filter). Returns each person with a `ref` so you can hand-pick the real
    decision-makers via `accepted_person_refs`. Use only when title searches fail."""
    counter = ctx.deps._counter
    if counter["n"] >= MAX_SEARCHES:
        return {"notice": "Search budget exhausted — finalise now with the results you already have."}
    counter["n"] += 1
    sid = counter["n"]
    people = await asyncio.to_thread(ctx.deps.apollo.search_all_people, ctx.deps.domain)
    ctx.deps.found[sid] = {"titles": ["<roster>"], "people": people}
    roster = [
        {"ref": f"{sid}.{i}", "name": (p.get("first_name") or p.get("name") or "").strip(), "title": p.get("title") or ""}
        for i, p in enumerate(people[:30])
    ]
    return {"search_id": sid, "result_count": len(people), "roster": roster}


async def source_prospects_for_company(
    domain: str,
    company_name: str,
    industry: str,
    job_titles: list[str],
) -> dict[str, Any]:
    """Run the sourcing agent for one company. Returns:
        {accepted: [apollo person dicts], decision_maker_titles, reasoning,
         confident, searches}. Never raises."""
    if not domain:
        return {"accepted": [], "decision_maker_titles": [], "reasoning": "no domain", "searches": 0}

    clean_titles = [t for t in (job_titles or []) if t and t.strip()][:8]
    deps = SourcingDeps(
        domain=domain,
        company_name=company_name or domain,
        industry=industry or "",
        job_titles=clean_titles,
        apollo=ApolloService(),
    )
    roles = "; ".join(clean_titles) or "(role unknown)"
    prompt = (
        f"Company: {deps.company_name} (industry: {deps.industry or 'unknown'}). "
        f"Open role(s): {roles}. Company domain: {domain}. "
        f"Identify and validate the decision-makers to contact."
    )

    try:
        res = await agent.run(prompt, deps=deps)
        out = res.output
    except Exception as e:  # noqa: BLE001
        logger.error("Sourcing agent failed for %s: %s", domain, e)
        return {"accepted": [], "decision_maker_titles": [], "reasoning": f"agent error: {e}", "searches": deps._counter["n"]}

    accepted_ids = {i for i in (out.accepted_search_ids or []) if i in deps.found}
    titles_tag = out.decision_maker_titles[:3]
    people: list[dict] = []
    seen: set[str] = set()

    def _keep(p: dict) -> bool:
        pid = p.get("id")
        if not pid or pid in seen or len(people) >= MAX_PEOPLE:
            return False
        seen.add(pid)
        p["_match_reasons"] = ["ai_sourced", *([f"target:{', '.join(titles_tag)}"] if titles_tag else [])]
        people.append(p)
        return True

    # Whole title-searches accepted by the agent.
    for sid in accepted_ids:
        for p in deps.found.get(sid, {}).get("people", []):
            _keep(p)

    # Hand-picked people from the plain-roster fallback (ref = "<sid>.<idx>").
    for ref in (out.accepted_person_refs or []):
        try:
            sid_s, idx_s = str(ref).split(".", 1)
            sid, idx = int(sid_s), int(idx_s)
        except (ValueError, AttributeError):
            continue
        roster = deps.found.get(sid, {}).get("people", [])
        if 0 <= idx < len(roster):
            _keep(roster[idx])

    # Defensive fallback: agent found people but marked nothing → keep the
    # largest TITLE search (never the plain roster, to avoid storing everyone).
    if not people:
        title_searches = {sid: d for sid, d in deps.found.items() if d.get("titles") != ["<roster>"]}
        if title_searches:
            best_sid, best = max(title_searches.items(), key=lambda kv: len(kv[1]["people"]))
            for p in best["people"]:
                _keep(p)

    return {
        "accepted": people,
        "decision_maker_titles": out.decision_maker_titles,
        "reasoning": out.reasoning,
        "confident": out.confident,
        "searches": deps._counter["n"],
    }
