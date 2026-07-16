"""Search Strategist agent (smart model, reasoning only, no tools).

Runs ONCE when the recruiter opens the discovery form. It reads the job title,
the JD, and whatever optional hints the recruiter gave, and proposes the filters
that will actually match real LinkedIn profiles — plus a broadening ladder for
the discovery loop to fall back on.

The problem it exists to solve: a job POSTING title is employer language ("SAP
Consultant FI"), while a LinkedIn headline is self-description ("SAP FICO
Consultant", "Senior Consultant - SAP Finance"). Searching the posting title
verbatim is why searches come back empty. The Strategist translates one into the
other.

Cheap and bounded by construction: one model call, no tools, no vendor spend, and
a request_limit that caps it even if the model tries to loop.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from app.services.sourcing.common import get_model, llm_available
from app.services.sourcing.models import (
    BroadeningStep, FilterRationale, SearchBrief, SearchFilters, SearchStrategy,
    enum_vocabulary_prompt,
)

logger = logging.getLogger(__name__)

INSTRUCTIONS = f"""You are a technical sourcing strategist. You turn a job opening
into a LinkedIn people-search that returns REAL candidates.

The single most important thing you understand: a job POSTING title is written in
employer/HR language, but you are searching PROFILE titles, which are written in
the member's own words. They are frequently different, and searching the posting
title verbatim is the #1 cause of a zero-result search.

Examples of the translation you must perform:
  • "SAP Consultant FI" → nobody's headline says that. Real titles: "SAP FICO
    Consultant", "SAP FI Consultant", "SAP Finance Consultant", "SAP FI/CO
    Consultant", "Senior SAP Consultant". FI is almost always paired with CO in
    practice — include the FICO variants.
  • "Java Developer II" → the "II" is an internal grade, never a headline. Use
    "Java Developer", "Software Engineer", "Backend Developer".
  • "Growth Marketing Ninja" → a real title is "Growth Marketing Manager",
    "Performance Marketing Manager", "Digital Marketing Manager".
  • "Cloud Architect (AWS)" → "Cloud Architect", "AWS Architect", "Solutions
    Architect", "Cloud Solutions Architect".

Rules for the filters you produce:
1. `currentJobTitles` — 4 to 8 titles people ACTUALLY carry on LinkedIn. Include
   the common abbreviation AND the expanded form when both are in use (SAP FICO /
   SAP Finance). Order strongest-match first. Never include internal grades
   (II, L3, Band 4), employment types (Contract, Permanent), or req codes.
   Never invent a title nobody uses just to look thorough.
2. `searchQuery` — a SHORT fuzzy keyword phrase (2-4 words), NOT the full title
   string. It is a broad keyword match, so put the domain in it ("SAP FICO"),
   not the whole posting title.
3. `locations` — a LinkedIn-recognisable place. Prefer the metro/city the job is
   in; use the country when the role is remote or the city is tiny. Never emit a
   full street address, a postcode, or an office name.
4. `seniorityLevel` / `function` / `yearsOfExperience` — infer from the JD, not
   the title alone. A JD asking for 8+ years and "lead the workstream" is Senior
   or Experienced Manager, whatever the title says. LEAVE A FILTER NULL when the
   JD doesn't support it — every filter you add shrinks the result set, and a
   wrong filter is far worse than a missing one.
5. Skills are NOT a filter on this actor. Encode skill signal in the titles and
   the query instead (e.g. must-have "S/4HANA" → add "SAP S/4HANA Consultant" as
   a title). Do not silently drop the signal.
6. If the recruiter named target companies, put them in `currentCompanies` — that
   is a deliberate poach and should be respected exactly.

Enum filters MUST use one of these codes (emit the CODE, not the label):
{enum_vocabulary_prompt()}

Also produce:
  • `interpretedRole` — one line naming what this job really is, in plain terms.
  • `titleReasoning` — one or two sentences on why the posting title does or
    doesn't work as a search term. This is shown to the recruiter, so be concrete
    and reference the actual title.
  • `rationale` — one short entry per non-empty filter, saying why. `field` must
    be the exact filter name.
  • `broadeningLadder` — 3 fallback attempts, tried in order ONLY if the search
    returns zero. Each step carries a COMPLETE filter set (not a diff), and each
    must be strictly broader than the one before. Sensible progression: drop the
    narrowest enum → generalise the titles → widen the location to the country.
    Step 3 should be broad enough that returning zero means the talent genuinely
    isn't on LinkedIn.
  • `confidence` — 0..1. Be honest: a vague one-line JD with no location is 0.3,
    not 0.9.
  • `warnings` — anything the recruiter should know (title is region-specific,
    the skill combination is rare, the location has a thin talent pool).

Be decisive and specific. Prefer fewer, higher-signal filters."""


def _build_agent() -> Agent:
    """Built lazily so importing this module never requires an API key."""
    return Agent(
        get_model("smart"),
        output_type=SearchStrategy,
        instructions=INSTRUCTIONS,
        retries=2,
    )


def _fallback(brief: SearchBrief) -> SearchStrategy:
    """The old literal-title prefill, as a graceful degrade.

    Used when no LLM key is configured or the agent call fails. The recruiter
    still gets a usable form — just without the translation — and `confidence: 0`
    plus a warning tells the UI (and them) that the AI did not run.
    """
    return SearchStrategy(
        interpretedRole=brief.jobTitle,
        titleReasoning="AI suggestions unavailable — prefilled from the job title as-is.",
        filters=SearchFilters(
            searchQuery=brief.jobTitle,
            currentJobTitles=[brief.jobTitle] if brief.jobTitle else [],
            locations=[brief.jobLocation] if brief.jobLocation else [],
        ),
        confidence=0.0,
        warnings=["AI suggestions unavailable — review these filters before searching."],
    )


def _brief_prompt(brief: SearchBrief) -> str:
    """Render the brief, with the JD truncated to keep the call cheap.

    12k chars matches the JD budget llm_extraction_service already uses, and is
    well past where a JD stops adding sourcing signal.
    """
    payload = brief.model_dump(exclude_defaults=True)
    jd = (payload.pop("jobDescription", "") or "")[:12000]
    out = [f"Job brief:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"]
    if jd:
        out.append(f"\nJob description:\n{jd}")
    out.append("\nProduce the SearchStrategy.")
    return "\n".join(out)


async def propose_strategy(brief: SearchBrief) -> SearchStrategy:
    """Propose search filters for a job. Never raises — falls back instead.

    A prefill failing must not block the recruiter from searching, so every error
    path degrades to `_fallback` (the previous literal-title behaviour).
    """
    if not brief.jobTitle.strip():
        return _fallback(brief)
    if not llm_available():
        logger.info("[Strategist] no LLM key configured — literal prefill")
        return _fallback(brief)

    try:
        result = await _build_agent().run(
            _brief_prompt(brief),
            # One reasoning call; the allowance covers a structured-output retry.
            usage_limits=UsageLimits(request_limit=3),
        )
        strategy = result.output
    except Exception as exc:  # noqa: BLE001 — prefill must never break the form
        logger.error("[Strategist] failed (%s) — literal prefill", exc, exc_info=True)
        return _fallback(brief)

    return _sanitize(strategy, brief)


def _sanitize(strategy: SearchStrategy, brief: SearchBrief) -> SearchStrategy:
    """Defensive clamps on model output (same spirit as account_intel's planner).

    The enum coercion already happened in SearchFilters' validator; this handles
    the structural things a validator can't: an empty proposal, a ladder that
    isn't ordered, and a runaway title list.
    """
    f = strategy.filters
    # An empty filter set is unusable — fall back rather than search on nothing.
    if f.is_empty():
        logger.warning("[Strategist] returned an empty filter set — literal prefill")
        return _fallback(brief)

    # Cap the title list: past ~10 the actor's OR-match returns noise.
    if len(f.currentJobTitles) > 10:
        f.currentJobTitles = f.currentJobTitles[:10]

    # A location the recruiter gave should never be dropped silently.
    if brief.jobLocation and not f.locations:
        f.locations = [brief.jobLocation]

    # Renumber the ladder so `step` is authoritative regardless of what came back.
    ladder: list[BroadeningStep] = []
    for i, step in enumerate(strategy.broadeningLadder[:5], start=1):
        step.step = i
        if not step.filters.is_empty():
            ladder.append(step)
    strategy.broadeningLadder = ladder

    # Rationale must reference real fields, or the UI renders orphan tooltips.
    valid = set(SearchFilters.model_fields)
    strategy.rationale = [r for r in strategy.rationale if r.field in valid]
    return strategy
