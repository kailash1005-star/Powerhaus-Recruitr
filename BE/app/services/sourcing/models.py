"""Typed contracts for agentic candidate sourcing.

One source of truth shared by the agents, the discovery loop, and the FastAPI
layer — the same role `account_intel/models.py` plays there.

The important model is `SearchFilters`: it is BOTH what the Strategist proposes
and what `apify_search_service.search()` consumes, so an agent can never emit a
filter set the actor would reject. Enum fields are validated against the actor's
own vocabulary (`ENUM_TABLES`) and coerced to its codes; an unrecognised value is
dropped rather than passed through, because the actor fails a whole run on a bad
enum instead of ignoring it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.services.apify_search_service import ENUM_TABLES, resolve_enum


# ─────────────────────────── Recruiter input ────────────────────────────

class SearchBrief(BaseModel):
    """Everything we can tell the Strategist about a role.

    Only `jobTitle` is required — every other field is an optional hint that
    sharpens the proposed filters. The recruiter fills what they know; the JD
    text (pulled from the job doc when present) carries most of the signal.
    """
    # Known from the job/pipeline, not typed by the recruiter:
    jobTitle: str
    jobLocation: str = ""
    companyName: str = ""
    companyIndustry: str = ""
    jobDescription: str = ""

    # Optional recruiter hints — the "tell the AI more" fields:
    seniorityHint: str = ""
    mustHaveSkills: List[str] = Field(default_factory=list)
    niceToHaveSkills: List[str] = Field(default_factory=list)
    minYears: Optional[float] = None
    targetIndustries: List[str] = Field(default_factory=list)
    # Competitors / talent pools worth poaching from.
    targetCompanies: List[str] = Field(default_factory=list)
    excludeCompanies: List[str] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    workModel: Literal["", "onsite", "hybrid", "remote"] = ""
    openToRelocation: bool = False
    notes: str = ""


# ─────────────────────────── Agent output ───────────────────────────────

class SearchFilters(BaseModel):
    """A concrete LinkedIn people-search filter set (one search attempt).

    Field names match `apify_search_service`'s filter keys exactly, so
    `model_dump(exclude_none=True)` is a valid `search()` argument.
    """
    # Fuzzy free-text. Keep SHORT — the actor treats it as a keyword match.
    searchQuery: str = ""
    # Real-world titles people actually carry. This is the field that fixes
    # "SAP Consultant FI" returning nothing.
    currentJobTitles: List[str] = Field(default_factory=list)
    pastJobTitles: List[str] = Field(default_factory=list)
    locations: List[str] = Field(default_factory=list)
    currentCompanies: List[str] = Field(default_factory=list)
    pastCompanies: List[str] = Field(default_factory=list)
    excludeCurrentCompanies: List[str] = Field(default_factory=list)
    excludeCurrentJobTitles: List[str] = Field(default_factory=list)
    profileLanguages: List[str] = Field(default_factory=list)
    # Enum filters — codes from the actor's vocabulary (see ENUM_TABLES).
    yearsOfExperience: Optional[str] = None
    seniorityLevel: Optional[str] = None
    function: Optional[str] = None
    companyHeadcount: Optional[str] = None
    recentlyChangedJobs: bool = False
    recentlyPostedOnLinkedin: bool = False

    @field_validator("yearsOfExperience", "seniorityLevel", "function",
                     "companyHeadcount", mode="before")
    @classmethod
    def _coerce_enum(cls, v: Any, info) -> Optional[str]:
        """Accept a code or a human title; store the code. Drop the unknown.

        The model is told to emit codes but happily emits "Senior" instead of
        "120" some of the time. `resolve_enum` takes either, and returning None
        for anything unrecognised means a hallucinated value silently drops the
        filter instead of failing the actor run.
        """
        return resolve_enum(info.field_name, v)

    def to_search_input(self) -> Dict[str, Any]:
        """Drop empties → the dict `ApifySearchService.search()` expects."""
        raw = self.model_dump(exclude_none=True)
        return {k: v for k, v in raw.items() if v not in ("", [], False)}

    def is_empty(self) -> bool:
        """True when there's nothing to search on (no query, no titles)."""
        return not (self.searchQuery.strip() or self.currentJobTitles)


class FilterRationale(BaseModel):
    """Why the agent chose one filter — surfaced next to the field in the UI."""
    field: str = Field(description="Exact filter field name, e.g. currentJobTitles")
    why: str = Field(
        default="",
        description="One short sentence, shown to the recruiter next to the field.",
    )


class BroadeningStep(BaseModel):
    """One pre-planned relaxation, tried in order when a search returns zero."""
    step: int = Field(ge=1, description="1-based order; step 1 is tried first.")
    action: Literal[
        "generalise_titles", "add_adjacent_titles", "widen_location",
        "drop_seniority", "widen_years", "drop_function",
        "drop_companies", "widen_query",
    ]
    detail: str = Field(
        default="",
        description=(
            "REQUIRED. One plain sentence naming what this step changes and why, "
            "referencing the actual values (e.g. 'Widen Walldorf to Germany — SAP "
            "talent clusters in the Rhein-Neckar metro, not the town'). Shown to "
            "the recruiter verbatim."
        ),
    )
    filters: SearchFilters = Field(
        description="The COMPLETE filter set to try at this step, not a diff.",
    )


class DomainAnchor(BaseModel):
    """The two-tier statement of what makes this role THIS role.

    ``coreTerms`` are the specialization words — the ones that separate this
    profession from its neighbours ("HCM", "SuccessFactors", "Payroll",
    "Entgeltabrechnung" for an SAP HCM role). ``ecosystemTerms`` are the
    platform/vendor words the role SHARES with different professions ("SAP" is
    carried by FI/CO consultants, Basis admins and HCM consultants alike).

    The distinction exists because the old single-tier anchor let "SAP FICO
    Consultant" pass as in-domain for an SAP HCM search — they share "SAP", the
    ecosystem word, which is precisely the part that carries no specialization
    signal. A proposed title is in-domain only if it carries a CORE term.
    """
    coreTerms: List[str] = Field(
        default_factory=list,
        description=(
            "Specialization words that make this role distinct from neighbouring "
            "roles on the same platform. Single words, lowercase. A title without "
            "any of these is a DIFFERENT profession."
        ),
    )
    ecosystemTerms: List[str] = Field(
        default_factory=list,
        description=(
            "Platform/vendor words shared across many professions (SAP, Oracle, "
            "AWS…). Matching one of these alone does NOT make a title in-domain."
        ),
    )

    def is_empty(self) -> bool:
        return not self.coreTerms


class ApolloPlan(BaseModel):
    """Apollo-specific people-search inputs, proposed alongside the Apify filters.

    Apollo matches differently from the LinkedIn actor, so it gets its OWN input
    rather than the Apify filter set reused. See the case study (§B): titles
    OR-expand (`include_similar_titles` stays on), skills go through free-text
    `q_keywords` (1–3 defining terms only — more AND-narrows), `locations` is the
    person's residence, and `seniorities` uses Apollo's own enum. There is no
    industries field — `person_industries[]` is not a real Apollo param.
    """
    # Title family + word-order variants ("SAP EWM Consultant", "Consultant SAP EWM").
    titles: List[str] = Field(default_factory=list)
    # The 1–3 defining skills, matched as free text across the profile.
    qKeywords: List[str] = Field(default_factory=list)
    # Where the candidate lives ("Koblenz, Germany"). NOT employer HQ.
    locations: List[str] = Field(default_factory=list)
    # Apollo person_seniorities codes: owner, founder, c_suite, partner, vp, head,
    # director, manager, senior, entry, intern. Kept sparse — a title family
    # already captures most of the seniority signal.
    seniorities: List[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.titles or self.qKeywords)


# Apollo's person_seniorities vocabulary (see the People Search API reference).
APOLLO_SENIORITIES: tuple[str, ...] = (
    "owner", "founder", "c_suite", "partner", "vp", "head",
    "director", "manager", "senior", "entry", "intern",
)


class SearchStrategy(BaseModel):
    """The Strategist's full proposal for one job — the prefill payload."""
    # The interpreted role, in words a LinkedIn member would recognise.
    interpretedRole: str = ""
    # The single interpreted, LinkedIn-real title that anchors BOTH engines and
    # headlines the review screen (e.g. "Senior SAP EWM/LES Consultant" →
    # "SAP EWM Consultant"). Falls back to interpretedRole/jobTitle when unset.
    focusTitle: str = ""
    # Why the recruiter's literal title would/wouldn't work as a search term.
    titleReasoning: str = ""
    filters: SearchFilters
    # Engine-appropriate Apollo inputs proposed from the same brief.
    apolloPlan: ApolloPlan = Field(default_factory=ApolloPlan)
    rationale: List[FilterRationale] = Field(default_factory=list)
    # What may never be relaxed away — see DomainAnchor. Enforced in code by the
    # discovery loop, not just prompted.
    domainAnchor: DomainAnchor = Field(default_factory=DomainAnchor)
    # Adjacent-specialty titles (same platform, neighbouring profession — e.g.
    # "HRIS Consultant" for an SAP HCM role). NEVER searched automatically:
    # they are offered to the recruiter as opt-in chips when the in-specialty
    # search comes up short. A recruiter click is the only thing that turns one
    # of these into a search term.
    adjacentTitles: List[str] = Field(default_factory=list)
    # Pre-planned fallbacks, ordered widest-last. The loop uses these as the
    # Broadener's hint and as its fallback if the Broadener call fails.
    broadeningLadder: List[BroadeningStep] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # Anything the recruiter should know (e.g. "this title is region-specific").
    warnings: List[str] = Field(default_factory=list)


class BroadenDecision(BaseModel):
    """The Broadener's answer after a zero-result attempt.

    `decision` is an action enum rather than a boolean on purpose: a negated flag
    ("shouldRetry: false") gets inverted by the model, which then returns a
    perfectly good broadened filter set flagged to be thrown away. Naming the two
    outcomes removes the polarity it has to infer.
    """
    decision: Literal["broaden", "give_up"] = Field(
        default="broaden",
        description=(
            "'broaden' = try the filters below as the next search. "
            "'give_up' = the filters are already broad and still return nothing, "
            "so another paid attempt is waste. Choose 'broaden' whenever there is "
            "still a filter left to relax."
        ),
    )
    action: str = Field(
        default="widen_query",
        description="Short label for what you relaxed, e.g. widen_location.",
    )
    reasoning: str = Field(
        default="",
        description=(
            "One plain sentence, shown to the recruiter, naming what changed and "
            "why, referencing the actual values."
        ),
    )
    filters: SearchFilters = Field(
        description="The COMPLETE next filter set, not a diff. Required for 'broaden'.",
    )


# ─────────────────────────── Run telemetry ──────────────────────────────

class SearchAttempt(BaseModel):
    """One executed search — persisted on the pipeline job for the UI timeline."""
    attempt: int
    # "initial" = the user's own filters; later attempts name their relaxation.
    action: str = "initial"
    reasoning: str = ""
    filters: Dict[str, Any] = Field(default_factory=dict)
    resultCount: int = 0
    # Per-channel raw counts when the attempt ran more than one search page
    # (e.g. {"title": 18, "keyword": 12}). Feeds the UI transparency panel.
    channelCounts: Optional[Dict[str, int]] = None
    at: datetime = Field(default_factory=datetime.utcnow)
    error: Optional[str] = None


def enum_vocabulary_prompt() -> str:
    """Render ENUM_TABLES as a prompt block.

    Generated from the actor's live tables rather than hardcoded, so a new
    seniority or function code reaches the agents automatically.
    """
    lines: List[str] = []
    for key, table in ENUM_TABLES.items():
        if key.startswith("exclude"):
            continue
        pairs = ", ".join(f'"{code}"={title}' for code, title in table.items())
        lines.append(f"  {key}: {pairs}")
    return "\n".join(lines)
