"""Broadener agent (fast model, reasoning only, no tools).

Called ONLY when a search attempt returns zero candidates. It sees the full
attempt history — every filter set already tried and what each returned — and
decides the next, strictly broader attempt.

THE TARGET IS LOCKED. The Broadener relaxes the net around the target —
enum filters, companies, location, profile language — and never the target
itself: ``currentJobTitles`` and ``searchQuery`` are clamped in code to the
initial attempt's values, whatever the model proposes. This is deliberate and
load-bearing: told "the titles are too specific", a model will happily relax
"SAP HCM Consultant" into "SAP Consultant" — a different profession sharing a
platform brand — and the actor ORs titles, so ONE off-domain entry floods the
results with the wrong people. Since the Strategist now emits the full
within-specialty synonym family up front, any title widening beyond that set
IS a change of target, and changing the target is the recruiter's decision
(the awaiting-input widen flow), never a fallback's.

Why an agent instead of just walking the Strategist's pre-planned ladder: the
ladder is written blind, before any evidence. Once we know that "SAP FICO
Consultant" in "Walldorf" returned zero, the reason matters — dead enums need
different relaxation than a dead location, and only the failure tells you which.
The ladder is still passed in as a hint, and is used verbatim (titles clamped)
as the fallback when this call fails, so we degrade to the planned path rather
than to nothing.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from app.services import prescreen_service as prescreen
from app.services.sourcing.common import (
    ECOSYSTEM_TOKENS, GENERIC_ROLE_WORDS, get_model, llm_available,
    title_in_domain,
)
from app.services.sourcing.models import (
    BroadenDecision, BroadeningStep, SearchAttempt, SearchBrief, SearchFilters,
    enum_vocabulary_prompt,
)

logger = logging.getLogger(__name__)

INSTRUCTIONS = f"""You are a sourcing strategist recovering from a FAILED LinkedIn
people-search: every attempt so far returned ZERO candidates.

Given the job brief and the full history of what was tried, produce the NEXT
search attempt. It must be STRICTLY BROADER than every attempt already made —
repeating or narrowing is a wasted paid API call.

THE TARGET IS LOCKED. `currentJobTitles` and `searchQuery` must be copied
UNCHANGED from the initial attempt — they already contain every title this
profession actually uses, and any further title change means searching for a
DIFFERENT job, which is the recruiter's decision, not yours. (This is also
enforced in code: title changes you propose are discarded.)

What you MAY relax, in THIS order — cheapest, most speculative filters first:
  1. Enum filters (`seniorityLevel`, `yearsOfExperience`, `function`,
     `companyHeadcount`). ALWAYS start here. They are INFERRED from the JD, not
     stated by the member, and profiles routinely leave the underlying data
     blank — so they silently exclude people who DO qualify. Several set at once
     is the single most common cause of a zero-result search.
  2. `currentCompanies` — restricts to a handful of employers. Drop it unless the
     recruiter explicitly asked to poach from them.
  3. `locations` — widen the city to the metro, then to the country. Talent
     clusters in a few metros, not the town the office is in.
  4. `profileLanguages` — only if the role plausibly exists in English too.

Widen ONE dimension per attempt where possible, so the result stays attributable.
Only widen two when the remaining budget is nearly spent.

Choose `decision: "broaden"` whenever ANY of those four dimensions is still left
to relax — that is the normal case, and it means "run the filters I'm giving you
next".

Choose `decision: "give_up"` when the last attempt was already the bare titles
plus a country with no enum filters and STILL returned zero. That means this
exact specialty isn't findable this way, another paid attempt only burns money,
and the recruiter will be shown the option to deliberately widen the specialty
instead. Say so in `reasoning`. If you are proposing a filter set you believe
in, the decision is "broaden" — never pair a real proposal with "give_up".

`reasoning` is shown to the recruiter: one plain sentence on what you changed and
why, referencing the actual values (e.g. "Dropped the Senior filter and widened
Walldorf to Germany — SAP consultants there rarely tag seniority"). No jargon.

Enum filters MUST use one of these codes (emit the CODE, not the label):
{enum_vocabulary_prompt()}"""


def _build_agent() -> Agent:
    return Agent(
        get_model("fast"),
        output_type=BroadenDecision,
        instructions=INSTRUCTIONS,
        retries=2,
    )


def lock_target(
    decision: BroadenDecision, attempts: List[SearchAttempt],
) -> BroadenDecision:
    """Clamp the decision's titles + query to the INITIAL attempt's values.

    The structural guarantee behind "widening never means a different job":
    whatever the model proposed, the target it searches is the target the
    recruiter approved. Runs on every path that produces a next attempt —
    agent proposal and planned-ladder fallback alike.
    """
    if not attempts:
        return decision
    initial = attempts[0].filters
    decision.filters.currentJobTitles = list(initial.get("currentJobTitles") or [])
    decision.filters.searchQuery = str(initial.get("searchQuery") or "")
    return decision


def _ladder_fallback(
    ladder: List[BroadeningStep], attempt_number: int,
    attempts: List[SearchAttempt],
) -> Optional[BroadenDecision]:
    """Use the Strategist's pre-planned step for this attempt, if there is one.

    `attempt_number` is 1-based over retries, matching BroadeningStep.step.
    Titles/query are clamped: new strategies already lock them at generation
    time, but ladders persisted before that change still carry
    "generalise_titles" steps — the clamp makes those safe too.
    """
    step = next((s for s in ladder if s.step == attempt_number), None)
    if step is None:
        return None
    return lock_target(BroadenDecision(
        decision="broaden",
        action=step.action,
        reasoning=step.detail or f"Planned fallback: {step.action}",
        filters=step.filters,
    ), attempts)


def _anchor_from_strategy(anchor: Optional[Dict[str, Any]]) -> List[str]:
    """Core terms from a persisted Strategist DomainAnchor dict, if usable."""
    if not isinstance(anchor, dict):
        return []
    core = anchor.get("coreTerms") or []
    return [str(t).strip().lower() for t in core if t and str(t).strip()]


def _domain_anchor(
    attempts: List[SearchAttempt], brief: Optional[SearchBrief] = None,
    strategy_anchor: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """The CORE terms that make the role THIS role.

    Two-tier: the Strategist's declared ``coreTerms`` win when present — it
    knows "HCM" is the specialty and "SAP" merely the platform. The fallback
    derives terms from the FIRST attempt's titles (the only uncontaminated
    statement of the domain), stripping generic role suffixes AND demoting
    ecosystem brands: "SAP" alone must never satisfy the anchor when a more
    specific word exists, because that is exactly how "SAP FICO Consultant"
    passed as in-domain for an SAP HCM search.

    When the initial attempt carried NO titles (a searchQuery-only search), the
    brief's job title is the next-best uncontaminated statement of the domain.
    """
    declared = _anchor_from_strategy(strategy_anchor)
    if declared:
        return sorted(t for t in declared if t not in GENERIC_ROLE_WORDS)

    core: set[str] = set()
    eco: set[str] = set()

    def _absorb(text: str) -> None:
        for t in prescreen.tokens(text):
            if t in GENERIC_ROLE_WORDS:
                continue
            (eco if t in ECOSYSTEM_TOKENS else core).add(t)

    if attempts:
        for title in attempts[0].filters.get("currentJobTitles") or []:
            _absorb(title)
    if not (core or eco) and brief is not None:
        for source in (brief.jobTitle, (attempts[0].filters.get("searchQuery") if attempts else "")):
            _absorb(source or "")
    if not core:
        # Brand-only domain ("Workday Consultant"): the brand IS the specialty.
        return sorted(eco)
    return sorted(core)


def _enforce_domain(
    decision: BroadenDecision, attempts: List[SearchAttempt],
    brief: Optional[SearchBrief] = None,
    strategy_anchor: Optional[Dict[str, Any]] = None,
) -> Optional[BroadenDecision]:
    """Strip proposed titles that abandoned the role's specialization.

    Defense-in-depth behind ``lock_target``: the clamp makes agent/ladder drift
    structurally impossible, and this guard covers every other path a title
    list can travel (recruiter-supplied widen sets are validated elsewhere;
    tests pin the semantics here). A title passes only if it carries a CORE
    term — an ecosystem brand ("SAP") alone is NOT enough, because that is the
    part of the title every neighbouring profession shares.

    Returns the decision with off-domain titles removed, or None to fall back
    to the Strategist's planned ladder when nothing on-domain survives.
    """
    anchor = _domain_anchor(attempts, brief, strategy_anchor)
    if not anchor:
        return decision  # nothing to anchor to — don't invent a constraint
    proposed = decision.filters.currentJobTitles or []
    if not proposed:
        return decision

    kept = [t for t in proposed if title_in_domain(t, anchor)]
    dropped = [t for t in proposed if t not in kept]

    if not dropped:
        return decision
    if not kept:
        logger.warning(
            "[Broadener] proposal abandoned the domain entirely (%s ⊄ %s) — planned ladder",
            proposed, anchor,
        )
        return None

    logger.info("[Broadener] dropped off-domain title(s) %s; kept %s (domain: %s)",
                dropped, kept, anchor)
    decision.filters.currentJobTitles = kept
    return decision


def _history_prompt(brief: SearchBrief, attempts: List[SearchAttempt],
                    strategy_anchor: Optional[Dict[str, Any]] = None) -> str:
    payload = brief.model_dump(exclude_defaults=True)
    payload.pop("jobDescription", None)  # the titles carry the signal by now
    lines = [
        f"Job brief:\n{json.dumps(payload, ensure_ascii=False, indent=2)}",
    ]
    # Name the domain explicitly. The brief's must-haves are what the candidate
    # will actually be scored against, so they define what may never be relaxed.
    anchor = _domain_anchor(attempts, brief, strategy_anchor)
    if anchor:
        lines.append(
            "\nDOMAIN (locked) — the titles and query are about this and must be "
            f"copied unchanged from attempt #1:\n  {', '.join(anchor)}"
        )
    if brief.mustHaveSkills:
        lines.append(
            "\nThe candidate will be scored against these must-have skills, so a "
            "search that returns people who plainly lack all of them is a failed "
            f"search however many results it gets:\n  {', '.join(brief.mustHaveSkills)}"
        )
    lines.append("\nAttempts so far (all returned zero):")
    for a in attempts:
        lines.append(
            f"  #{a.attempt} [{a.action}] → {a.resultCount} results"
            f"{f' · error: {a.error}' if a.error else ''}\n"
            f"     filters: {json.dumps(a.filters, ensure_ascii=False)}"
        )
    lines.append("\nProduce the next, strictly broader attempt as a BroadenDecision.")
    return "\n".join(lines)


async def next_attempt(
    brief: SearchBrief,
    attempts: List[SearchAttempt],
    ladder: Optional[List[BroadeningStep]] = None,
    strategy_anchor: Optional[Dict[str, Any]] = None,
) -> Optional[BroadenDecision]:
    """Decide the next broadened search, or None to stop.

    Returns None when the agent says stop, when no LLM is configured and the
    ladder is exhausted, or when the proposal is unusable. Every returned
    decision has its titles/query clamped to the initial attempt's — see
    ``lock_target``.
    """
    ladder = ladder or []
    # attempts includes the initial search, so the 1st retry is attempt len(...).
    retry_number = len(attempts)

    if not llm_available():
        logger.info("[Broadener] no LLM key — using planned ladder step %d", retry_number)
        return _ladder_fallback(ladder, retry_number, attempts)

    try:
        result = await _build_agent().run(
            _history_prompt(brief, attempts, strategy_anchor),
            usage_limits=UsageLimits(request_limit=3),
        )
        decision = result.output
    except Exception as exc:  # noqa: BLE001 — degrade to the planned ladder
        logger.error("[Broadener] failed (%s) — planned ladder step %d",
                     exc, retry_number, exc_info=True)
        return _ladder_fallback(ladder, retry_number, attempts)

    if decision.decision == "give_up":
        logger.info("[Broadener] stopping: %s", decision.reasoning)
        return None

    # The target is not the model's to change — clamp before any other check,
    # so "already tried" is judged on what would actually run.
    decision = lock_target(decision, attempts)

    if decision.filters.is_empty():
        logger.warning("[Broadener] proposed an empty filter set — planned ladder")
        return _ladder_fallback(ladder, retry_number, attempts)
    if _already_tried(decision.filters, attempts):
        logger.warning("[Broadener] proposed a filter set already tried — planned ladder")
        fallback = _ladder_fallback(ladder, retry_number, attempts)
        if fallback is not None and _already_tried(fallback.filters, attempts):
            return None
        return fallback
    return decision


def _already_tried(filters: SearchFilters, attempts: List[SearchAttempt]) -> bool:
    """Guard against paying for the exact same search twice.

    Same idea as account_intel's ToolGuard dedup: an identical call returns
    nothing new, so refuse it rather than spend on it.
    """
    candidate = _fingerprint(filters.to_search_input())
    return any(_fingerprint(a.filters) == candidate for a in attempts)


def _fingerprint(filters: dict) -> str:
    """Order-insensitive identity for a filter set."""
    norm = {
        k: sorted(str(x).strip().lower() for x in v) if isinstance(v, list)
        else str(v).strip().lower()
        for k, v in sorted(filters.items())
    }
    return json.dumps(norm, sort_keys=True)
