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

from app.services.sourcing.common import (
    GENERIC_ROLE_WORDS, derive_anchor_terms, get_model, llm_available,
    title_in_domain,
)
from app.services.sourcing.models import (
    BroadeningStep, DomainAnchor, FilterRationale, SearchBrief, SearchFilters,
    SearchStrategy, enum_vocabulary_prompt,
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
1. `currentJobTitles` — 4 to 10 titles people ACTUALLY carry on LinkedIn, and
   ALL of them inside the SAME specialization. This is the full within-specialty
   synonym family: the common abbreviation AND the expanded form (SAP FICO / SAP
   Finance), the local-language and English variants, the vendor's product names
   for the same job (SAP HCM → SAP SuccessFactors, SAP HR, SAP Payroll/PY).
   Order strongest-match first. Never include internal grades (II, L3, Band 4),
   employment types (Contract, Permanent), or req codes. Never invent a title
   nobody uses just to look thorough — and NEVER pad the list with a
   neighbouring specialization (an SAP HCM search must not contain SAP FICO:
   same platform, different profession; that goes in `adjacentTitles`).
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
5. Skills are NOT a filter on this actor, but `mustHaveSkills` in the brief is
   the EXACT list the candidate will later be scored against — a candidate who
   carries none of them cannot pass, however good the title looks. So the
   must-haves are your primary aiming input, not a footnote: encode them in the
   titles and the query (must-have "S/4HANA" → add "SAP S/4HANA Consultant" as a
   title; must-have "Entgeltabrechnung" → "Entgeltabrechner", "Payroll
   Specialist", "Personalsachbearbeiter Entgeltabrechnung"). Never silently drop
   the signal, and never propose titles whose holders would plainly have none of
   the must-haves.
6. If the recruiter named target companies, put them in `currentCompanies` — that
   is a deliberate poach and should be respected exactly.
7. LANGUAGE. People describe themselves in the language they work in. If the JD
   or its must-have skills are in a language other than English (German
   "Entgeltabrechnung", French "Chargé de recrutement"), the people you want have
   native-language headlines, and English titles will not find them. Emit titles
   in THAT language — add the English variant only where it is genuinely also in
   use locally (e.g. "Payroll Specialist" is common in Germany; "Labour Law
   Clerk" is not). Set `profileLanguages` to that language when the role is
   plainly local. Domain vocabulary is the strongest language signal — trust it
   over the posting title.

Enum filters MUST use one of these codes (emit the CODE, not the label):
{enum_vocabulary_prompt()}

Also produce:
  • `interpretedRole` — one line naming what this job really is, in plain terms.
  • `titleReasoning` — one or two sentences on why the posting title does or
    doesn't work as a search term. This is shown to the recruiter, so be concrete
    and reference the actual title.
  • `rationale` — one short entry per non-empty filter, saying why. `field` must
    be the exact filter name.
  • `domainAnchor` — the words that make this role THIS role, in two tiers.
    `coreTerms`: the specialization words that separate it from its neighbours
    (for SAP HCM: hcm, successfactors, payroll, hr, personal). `ecosystemTerms`:
    the platform/vendor words it SHARES with different professions (for SAP HCM:
    sap — FI/CO consultants and Basis admins carry it too). Single lowercase
    words. This is enforced in code: any title that carries no core term is
    dropped as off-domain, so a core-term list that is too narrow throws away
    good titles and one that wrongly contains an ecosystem word lets the wrong
    profession in.
  • `adjacentTitles` — 3 to 6 titles from NEIGHBOURING specializations that a
    recruiter might deliberately widen into when the exact specialty pool is
    thin ("HRIS Consultant", "Workday HCM Consultant" for an SAP HCM role).
    These are NEVER searched automatically — they become opt-in suggestions the
    recruiter can click. Do NOT put in-specialty synonyms here; those belong in
    `currentJobTitles`.
  • `broadeningLadder` — 3 fallback attempts, tried in order ONLY if the search
    returns zero. Each step carries a COMPLETE filter set (not a diff), and each
    must be strictly broader than the one before. The titles and searchQuery are
    LOCKED: every step keeps `currentJobTitles` and `searchQuery` exactly as in
    your main filters — steps relax ONLY the other dimensions (drop the
    narrowest enum → drop companies → widen the location to the country → drop
    profileLanguages). Changing the target is the recruiter's decision, never a
    fallback's. Step 3 should be broad enough that returning zero means the
    talent genuinely isn't findable this way.
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
    core, eco = derive_anchor_terms([brief.jobTitle])
    return SearchStrategy(
        interpretedRole=brief.jobTitle,
        titleReasoning="AI suggestions unavailable — prefilled from the job title as-is.",
        filters=SearchFilters(
            searchQuery=brief.jobTitle,
            currentJobTitles=[brief.jobTitle] if brief.jobTitle else [],
            locations=[brief.jobLocation] if brief.jobLocation else [],
        ),
        domainAnchor=DomainAnchor(coreTerms=core, ecosystemTerms=eco),
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

    # ── Domain anchor: validate the LLM's, or derive one ────────────────────
    # The anchor is load-bearing (the Broadener guard and the widen-suggestions
    # flow both read it), so it must ALWAYS exist and always be self-consistent
    # with the proposed titles.
    anchor = strategy.domainAnchor
    anchor.coreTerms = [t.strip().lower() for t in anchor.coreTerms if t and t.strip()][:12]
    anchor.ecosystemTerms = [t.strip().lower() for t in anchor.ecosystemTerms if t and t.strip()][:6]
    # A "core" term that is really a generic role word (consultant, manager…)
    # would let every profession through — strip them.
    anchor.coreTerms = [t for t in anchor.coreTerms if t not in GENERIC_ROLE_WORDS]
    if anchor.is_empty():
        core, eco = derive_anchor_terms([brief.jobTitle, *f.currentJobTitles])
        anchor.coreTerms, anchor.ecosystemTerms = core, eco
    else:
        # Self-consistency: if the anchor rejects most of the model's OWN titles,
        # the anchor is wrong (too narrow), not the titles — rebuild it from them.
        titles = f.currentJobTitles or []
        if titles:
            passing = sum(1 for t in titles if title_in_domain(t, anchor.coreTerms))
            if passing * 2 < len(titles):
                logger.warning(
                    "[Strategist] anchor %s rejects %d/%d of its own titles — rebuilt",
                    anchor.coreTerms, len(titles) - passing, len(titles),
                )
                core, eco = derive_anchor_terms([brief.jobTitle, *titles])
                anchor.coreTerms, anchor.ecosystemTerms = core, eco

    # Adjacent titles are recruiter-opt-in ONLY. Anything that is actually
    # IN-specialty belongs in currentJobTitles, so de-dupe across the two lists,
    # cap, and drop empties.
    seen = {t.strip().lower() for t in f.currentJobTitles}
    strategy.adjacentTitles = [
        t.strip() for t in strategy.adjacentTitles
        if t and t.strip() and t.strip().lower() not in seen
    ][:6]

    # Renumber the ladder so `step` is authoritative regardless of what came
    # back — and LOCK its titles/query: a fallback step may relax enums,
    # companies, location and language, never the target. This is the code-level
    # guarantee behind "widening never means a different job".
    ladder: list[BroadeningStep] = []
    for i, step in enumerate(strategy.broadeningLadder[:5], start=1):
        step.step = i
        step.filters.currentJobTitles = list(f.currentJobTitles)
        step.filters.searchQuery = f.searchQuery
        if not step.filters.is_empty():
            ladder.append(step)
    strategy.broadeningLadder = ladder

    # Rationale must reference real fields, or the UI renders orphan tooltips.
    valid = set(SearchFilters.model_fields)
    strategy.rationale = [r for r in strategy.rationale if r.field in valid]
    return strategy
