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

from app.services import location_catalog
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
4. `seniorityLevel` / `function` / `yearsOfExperience` / `companyHeadcount` —
   these four are LinkedIn's OWN INFERRED fields, and they are missing or wrong
   on a large share of real profiles (a "Senior SAP EWM Consultant" with 8 years
   is routinely tagged Entry or left blank). Each one you set is AND-ed and
   silently DROPS good candidates whose inferred value doesn't match. So your
   DEFAULT for all four is NULL (Any). Only set one when the JD gives
   UNAMBIGUOUS support for it AND you are highly confident — and even then prefer
   to encode seniority in the TITLE WORDS ("Senior SAP EWM Consultant") rather
   than in `seniorityLevel`. A wrong filter here is far worse than a missing one;
   when in doubt, leave it null and add a `rationale` note saying you kept it Any
   to protect recall.
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

Both search engines (LinkedIn and Apollo) run off this ONE proposal — the Apollo
inputs are derived from your titles/locations/skills in code, so you do NOT need
to restate them. Put ALL of your effort into one high-quality, in-specialty title
family and a short query. Getting the SAME titles right serves both engines.

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


def _looks_like_full_title(search_query: str, job_title: str) -> bool:
    """True when searchQuery is really the posting title (the #1 zero-result
    cause) — verbatim, contains it, or just too long to be a keyword phrase."""
    sq = _toks(search_query)
    if not sq:
        return False
    if len(sq) > 4:
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
