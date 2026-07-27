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
import re
from typing import Optional

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from app.services.sourcing import location_catalog
from app.services.sourcing.common import (
    ECOSYSTEM_TOKENS, GENERIC_ROLE_WORDS, derive_anchor_terms, get_model,
    llm_available, title_in_domain,
)
from app.services.sourcing.models import (
    ApolloPlan, BroadeningStep, DomainAnchor,
    FilterRationale, SearchBrief, SearchFilters, SearchStrategy,
    enum_vocabulary_prompt,
)

logger = logging.getLogger(__name__)

INSTRUCTIONS = f"""You are a master technical sourcing strategist. You analyze job openings and construct high-converting LinkedIn search queries and filter sets that return REAL, qualified candidates.

The core problem you solve:
Job POSTINGS are written in employer/HR language ("SAP Consultant FI"), whereas LinkedIn PROFILES are self-described headlines ("SAP FICO Consultant", "Senior SAP Finance Consultant"). Searching posting titles verbatim causes zero-result searches. You translate employer requirements into profile reality.

Examples of translation:
  • "SAP Consultant FI" → "SAP FICO Consultant", "SAP FI Consultant", "SAP Finance Consultant", "SAP FI/CO Consultant", "Senior SAP Consultant".
  • "Java Developer II" → "Java Developer", "Software Engineer", "Backend Developer".
  • "Cloud Architect (AWS)" → "Cloud Architect", "AWS Architect", "Solutions Architect", "Cloud Solutions Architect".

Rules for the filters you produce:

1. `searchQuery` — LINKEDIN BOOLEAN SEARCH QUERY (STRICTLY CONCISE & STRAIGHT TO THE POINT):
   Build a clean, high-converting LinkedIn Boolean search query string using standard LinkedIn syntax.
   CRITICAL QUERY CONSTRAINTS:
   - KEEP IT STRAIGHT TO THE POINT: Limit the query to 3 TO 6 KEYWORDS TOTAL (never exceed 6 to 8 terms).
   - FOCUS ON TARGET JOB TITLES: The query should primarily OR-combine 3 to 5 core target title variations or 1 primary domain keyword + core titles.
   - NO LOCATIONS IN SEARCH QUERY: NEVER include country, state, or city names (e.g., "Germany", "Bavaria", "Munich", "Deutschland") in `searchQuery`. Locations MUST be passed exclusively via the dedicated `locations` filter parameter.
   - NO LONG SKILL OR TOOL LISTS: Do NOT append long AND-blocks of secondary skills, tools, or server technologies (e.g. do NOT add AND ("Windows Server" OR "Linux Server" OR "Virtualization" OR "Backup")). Over-constraining with AND-blocks causes zero-result searches on LinkedIn.
   - Operators MUST be in UPPERCASE (`OR`, `AND`, `NOT`). Use double quotes `" "` for multi-word exact title phrases.

   Good Examples of `searchQuery`:
     • For IT System Administrator:
       `("IT-Systemadministrator" OR "IT System Administrator" OR "Systemadministrator" OR "System Administrator" OR "Network Administrator")`
     • For SAP Retail Sales Executive:
       `("SAP Retail" OR "SAP CAR") AND ("Account Executive" OR "Sales Manager")`
     • For Cloud Architect:
       `("Cloud Architect" OR "AWS Architect" OR "Solutions Architect")`

2. `currentJobTitles` — 4 to 8 titles real people carry on LinkedIn in the SAME specialization.
   - Include expanded forms, common abbreviations, local-language and English terms.
   - Never include internal grades (II, L3, Band 4), employment types (Contract, Freelance), or req codes.
   - For bilingual markets (DACH, Europe, LatAm, Asia), include BOTH local and English titles (e.g., "IT-Systemadministrator" and "System Administrator").

3. `yearsOfExperience` — DERIVED FROM JOB DESCRIPTION:
   - Read the job description and brief to determine required experience:
     • "1": Less than 1 year (entry level / junior)
     • "2": 1 to 2 years
     • "3": 3 to 5 years (mid level)
     • "4": 6 to 10 years (senior / lead)
     • "5": More than 10 years (principal / executive / director)
   - If not explicitly required or implied in the JD, leave as NULL / empty (Any) to preserve candidate recall.

4. `locations` — Clean LinkedIn location strings (e.g., "Bavaria, Germany", "Germany", "Munich, Germany"). Prefer city/metro area or federal state; use country for remote roles.

5. Inferred LinkedIn Filters (`seniorityLevel`, `function`, `companyHeadcount`, `yearsAtCurrentCompany`):
   - Only set when explicitly supported by the JD; default to NULL (Any) when uncertain.

6. Exclusions (`excludeCurrentJobTitles`, `excludeCurrentCompanies`, `excludeLocations`, etc.):
   - Use to exclude unwanted roles (e.g., exclude "Freelancer", "Working Student" if searching for permanent employees).

Enum filters MUST use one of these codes (emit the CODE, not the label):
{enum_vocabulary_prompt()}

Both search engines (LinkedIn and Apollo) run off this ONE proposal — the Apollo
inputs are derived from your titles/locations/skills in code, so you do NOT need
to restate them. Put ALL of your effort into one high-quality, in-specialty title
family and a crisp Boolean searchQuery. Getting the SAME titles right serves both engines.

Also produce:
  • `focusTitle` — the SINGLE best LinkedIn-real title for this role, the one you
    would type first ("Senior SAP EWM/LES Consultant" → "SAP EWM Consultant").
    It anchors both search engines and headlines the review screen. Keep the
    seniority word IN it when the role is senior. It MUST be a real profile title
    (never the raw posting title), and should be the strongest entry of
    `currentJobTitles`.
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
    your main filters. `locations` may change in exactly ONE way, on the final
    step only: a city widened to its OWN federal state ("Bamberg, Bavaria,
    Germany" → "Bavaria, Germany") — never the country, never another state.
    Ladder shape: drop the narrowest enum → drop companies / profileLanguages →
    widen city to its own state. Changing the target — or searching beyond the
    state — is the recruiter's decision, never a fallback's. The final step
    should be broad enough that returning zero means the talent genuinely isn't
    findable this way within that state.
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
        focusTitle=brief.jobTitle,
        titleReasoning="AI suggestions unavailable — prefilled from the job title as-is.",
        filters=SearchFilters(
            searchQuery=brief.jobTitle,
            currentJobTitles=[brief.jobTitle] if brief.jobTitle else [],
            locations=[brief.jobLocation] if brief.jobLocation else [],
        ),
        apolloPlan=ApolloPlan(
            titles=[brief.jobTitle] if brief.jobTitle else [],
            qKeywords=list(brief.mustHaveSkills[:3]),
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


# ── Title / query / location quality clamps ─────────────────────────────────
# These catch the two hallucination shapes the friction analysis found on hard
# inputs (verbatim posting title as the search query; brand+module fragments
# like "SAP CO"/"SAP PS" as titles) and guarantee both engines get the SAME,
# correctly-spelled locations. A validator can't do this — it's about the CONTENT
# of the fields, not their types.

_WORD_RE = re.compile(r"[0-9a-zA-Zäöüßéèêëàâçîïôûùñ]+", re.UNICODE)

# Apify seniorityLevel code → Apollo person_seniorities code. Only used to carry
# a seniority the recruiter/AI explicitly set over to Apollo; left sparse.
_SENIORITY_APIFY_TO_APOLLO = {
    "100": "entry", "110": "entry", "120": "senior", "130": "senior",
    "200": "manager", "210": "manager", "220": "director",
    "300": "vp", "310": "c_suite", "320": "owner",
}


def _toks(s: Optional[str]) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(s or "")]


def _dedupe(xs: list[str], cap: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for x in xs:
        t = (x or "").strip()
        k = t.lower()
        if t and k not in seen:
            out.append(t)
            seen.add(k)
    return out[:cap]


def _short_query_from(title: str) -> str:
    """A SHORT fuzzy keyword phrase from a real title: drop trailing role words,
    keep the specialization, cap 3 tokens. 'SAP EWM Consultant' → 'SAP EWM'."""
    parts = [p for p in (title or "").replace("/", " ").split() if p]
    while parts and parts[-1].lower() in GENERIC_ROLE_WORDS:
        parts.pop()
    if not parts:
        parts = [p for p in (title or "").split() if p]
    return " ".join(parts[:3]).strip()


def _is_boolean_query(query: str) -> bool:
    """True if query contains Boolean search operators or grouping symbols."""
    q = (query or "").upper()
    return " OR " in q or " AND " in q or " NOT " in q or "(" in q or '"' in q


def _looks_like_full_title(search_query: str, job_title: str) -> bool:
    """True when searchQuery is really the verbatim posting title (the #1 zero-result cause)."""
    if _is_boolean_query(search_query):
        return False
    sq = _toks(search_query)
    if not sq:
        return False
    if len(sq) > 5:
        return True
    s = " ".join(sq)
    jt = " ".join(_toks(job_title))
    return bool(jt) and (s == jt or jt in s)


def _is_degenerate_title(title: str) -> bool:
    """A brand+module fragment nobody carries as a headline: 'SAP CO', 'SAP PS'.
    ≤2 tokens, contains an ecosystem brand, and no profession word."""
    toks = _toks(title)
    if not toks or len(toks) > 2:
        return False
    return any(t in ECOSYSTEM_TOKENS for t in toks) and not any(
        t in GENERIC_ROLE_WORDS for t in toks)


def _normalize_locations(locs: list[str]) -> list[str]:
    """Canonicalise each location to its catalogue label ('Frankfurt am Main' →
    'Frankfurt, Germany'; 'kolenz, germany' → 'Koblenz, Germany'), deduped. An
    unrecognised place is kept as-typed. This is what makes the two engines
    receive identical, correctly-spelled locations."""
    out: list[str] = []
    seen: set[str] = set()
    for loc in (locs or []):
        raw = (loc or "").strip()
        if not raw:
            continue
        canon = location_catalog.normalize(raw) or raw
        k = canon.lower()
        if k not in seen:
            out.append(canon)
            seen.add(k)
    return out


def _derive_apollo_plan(
    f: SearchFilters, brief: SearchBrief, focus_title: str, core_terms: list[str],
) -> ApolloPlan:
    """Build the Apollo people-search input from the SAME cleaned Apify plan.

    Single source of truth — the recruiter edits one set of titles/locations/
    skills and both engines get consistent, correctly-spelled input. This is the
    structural cure for the 'Koblenz' (Apify) vs 'Kolenz' (Apollo) divergence:
    there is no second, independently-hallucinated Apollo location to drift.
      • titles      — the cleaned title family (+ focus). Apollo OR-expands.
      • qKeywords   — the 1–3 defining skills (must-haves, else the anchor's core
                      specialization terms). Apollo ANDs these, so kept ≤3.
      • locations   — the SAME normalized locations as Apify.
      • seniorities — carried over only if a seniority was explicitly set.
    """
    titles = _dedupe([focus_title, *(f.currentJobTitles or [])], 12)
    qkw = _dedupe(list(brief.mustHaveSkills or []), 3)
    if not qkw:
        qkw = _dedupe(list(core_terms or []), 3)
    locs = _dedupe(list(f.locations or ([brief.jobLocation] if brief.jobLocation else [])), 5)
    seniorities: list[str] = []
    if f.seniorityLevel:
        code = _SENIORITY_APIFY_TO_APOLLO.get(str(f.seniorityLevel))
        if code:
            seniorities = [code]
    return ApolloPlan(titles=titles, qKeywords=qkw, locations=locs, seniorities=seniorities)


def _map_min_years_to_enum(min_years: float) -> str:
    """Convert a numeric years requirement to HarvestAPI enum code ('1'..'5')."""
    if min_years < 1:
        return "1"
    elif min_years < 3:
        return "2"
    elif min_years < 6:
        return "3"
    elif min_years < 10:
        return "4"
    else:
        return "5"


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

    # Auto-map brief.minYears if experience filter wasn't explicitly set by the model
    if not f.yearsOfExperience and brief.minYears is not None:
        f.yearsOfExperience = _map_min_years_to_enum(brief.minYears)

    # ── Title family: drop brand+module fragments ("SAP CO", "SAP PS") that no
    # one carries as a headline, then dedupe and cap at 10 (past that the actor's
    # OR-match returns noise). The verbatim posting title is LEFT IN — it is often
    # a real headline too, and even when it isn't it just matches nobody (harmless
    # noise), whereas the fragments actively mislead. If cleaning would empty the
    # list we keep the original (is_empty already passed).
    cleaned: list[str] = []
    seen_t: set[str] = set()
    for t in f.currentJobTitles:
        t = (t or "").strip()
        key = " ".join(_toks(t))
        if not key or key in seen_t:
            continue
        if _is_degenerate_title(t):
            continue
        cleaned.append(t)
        seen_t.add(key)
    if cleaned:
        f.currentJobTitles = cleaned[:10]
    elif len(f.currentJobTitles) > 10:
        f.currentJobTitles = f.currentJobTitles[:10]

    # The strongest REAL title: the first that is neither the posting title
    # restated (employer language) nor a fragment. Seeds the short query and the
    # focus title so neither inherits the model's junk when it emitted some.
    best_title = next(
        (t for t in f.currentJobTitles
         if not _looks_like_full_title(t, brief.jobTitle) and not _is_degenerate_title(t)),
        (f.currentJobTitles[0] if f.currentJobTitles
         else (strategy.focusTitle or brief.jobTitle)),
    )

    # A location the recruiter gave should never be dropped silently; and every
    # location is canonicalised so the two engines get the SAME, correctly-spelled
    # place (the 'Koblenz'/'Kolenz' divergence, fixed at the source).
    if brief.jobLocation and not f.locations:
        f.locations = [brief.jobLocation]
    f.locations = _normalize_locations(f.locations)

    # ── searchQuery: a SHORT keyword phrase, never the posting title ───────────
    # The full-title searchQuery is the documented #1 zero-result cause; when the
    # model emits it (or nothing), derive a short phrase from the strongest title.
    if not f.searchQuery.strip() or _looks_like_full_title(f.searchQuery, brief.jobTitle):
        derived = _short_query_from(best_title)
        if derived:
            f.searchQuery = derived

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
    # back — and LOCK its titles/query, clamp its locations: a fallback step may
    # relax enums, companies and language, plus at most widen a city to its OWN
    # federal state (location_catalog.clamp_locations — same rule the Broadener
    # enforces, so the two paths can't diverge). Never the country, never
    # another state: that's the recruiter's next run. This is the code-level
    # guarantee behind "widening never means a different job — or a different
    # place" (added after a Bamberg search silently widened to state level and,
    # via the vendor's loose filter, went Germany-wide).
    ladder: list[BroadeningStep] = []
    for i, step in enumerate(strategy.broadeningLadder[:5], start=1):
        step.step = i
        step.filters.currentJobTitles = list(f.currentJobTitles)
        step.filters.searchQuery = f.searchQuery
        step.filters.locations = location_catalog.clamp_locations(
            list(f.locations or []), list(step.filters.locations or []))
        if not step.filters.is_empty():
            ladder.append(step)
    strategy.broadeningLadder = ladder

    # Rationale must reference real fields, or the UI renders orphan tooltips.
    valid = set(SearchFilters.model_fields)
    strategy.rationale = [r for r in strategy.rationale if r.field in valid]

    # ── focusTitle: always present, and never a fragment or a bare posting-title
    # restatement — headline the review screen with the strongest real title.
    if (not (strategy.focusTitle or "").strip()
            or _is_degenerate_title(strategy.focusTitle)
            or _looks_like_full_title(strategy.focusTitle, brief.jobTitle)):
        strategy.focusTitle = best_title or strategy.interpretedRole or brief.jobTitle

    # ── Apollo plan: DERIVED in code from the cleaned Apify plan, not trusted
    # from the model. One source of truth → the two engines can never diverge
    # (this is the structural fix for the 'Koblenz' vs 'Kolenz' bug), and the
    # model has ~40% less to emit, so it spends its budget on the title family.
    strategy.apolloPlan = _derive_apollo_plan(
        f, brief, strategy.focusTitle, anchor.coreTerms)
    return strategy
