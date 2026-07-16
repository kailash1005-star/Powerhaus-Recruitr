"""Broadener agent (fast model, reasoning only, no tools).

Called ONLY when a search attempt returns zero candidates. It sees the full
attempt history — every filter set already tried and what each returned — and
decides the next, strictly broader attempt.

Why an agent instead of just walking the Strategist's pre-planned ladder: the
ladder is written blind, before any evidence. Once we know that "SAP FICO
Consultant" in "Walldorf" returned zero, the reason matters — a dead title needs
different relaxation than a dead location, and only the failure tells you which.
The ladder is still passed in as a hint, and is used verbatim as the fallback
when this call fails, so we degrade to the planned path rather than to nothing.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from app.services import prescreen_service as prescreen
from app.services.sourcing.common import get_model, llm_available
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

THE ONE RULE YOU MUST NOT BREAK: widening means casting a wider net for THIS job.
It NEVER means searching for a different job. The role's domain vocabulary — the
words that make this role this role ("Entgeltabrechnung", "Payroll", "FICO",
"S/4HANA") — must survive every attempt. Relaxing "Entgeltabrechner" to "SAP
Consultant" or "HR Specialist" is NOT broadening: payroll clerks and SAP
consultants are different professions, and that search returns a pile of people
who cannot do the job. Zero results means "widen the net", never "change the
target". If the only way you can widen further is by abandoning the domain, the
answer is `give_up`, not a search for someone else.

Relax in THIS order — cheapest, most speculative filters FIRST, and touch the
titles LAST:
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
  5. TITLES, and only once 1-4 are exhausted. Generalise WITHIN the domain and
     keep the domain word: "Sachbearbeiter Entgeltabrechnung" → "Entgeltabrechner"
     → "Payroll Specialist" → "Payroll" (all still payroll). Add adjacent titles
     people genuinely move between INSIDE the profession. Never climb out to a
     generic title ("Consultant", "Specialist", "Manager") — those match everyone
     and nobody.

Widen ONE dimension per attempt where possible, so the result stays attributable.
Only widen two when the remaining budget is nearly spent.

Choose `decision: "broaden"` whenever ANY filter is still left to relax — that is
the normal case, and it means "run the filters I'm giving you next".

Choose `decision: "give_up"` ONLY when the last attempt was already a bare title
plus a country with no enum filters and STILL returned zero. That means the
talent isn't findable this way and another paid attempt only burns money. Say so
in `reasoning`. If you are proposing a filter set you believe in, the decision is
"broaden" — never pair a real proposal with "give_up".

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


def _ladder_fallback(
    ladder: List[BroadeningStep], attempt_number: int,
) -> Optional[BroadenDecision]:
    """Use the Strategist's pre-planned step for this attempt, if there is one.

    `attempt_number` is 1-based over retries, matching BroadeningStep.step.
    """
    step = next((s for s in ladder if s.step == attempt_number), None)
    if step is None:
        return None
    return BroadenDecision(
        decision="broaden",
        action=step.action,
        reasoning=step.detail or f"Planned fallback: {step.action}",
        filters=step.filters,
    )


# Role-suffix words that describe SENIORITY or FORM, not the profession. They are
# what every title has in common, so they can never evidence that a proposed title
# is still the same job: "Payroll Specialist" and "HR Specialist" share
# "specialist" and are different professions.
_GENERIC_ROLE_WORDS = {
    "consultant", "consulting", "berater", "beratung", "specialist", "spezialist",
    "manager", "management", "engineer", "analyst", "administrator", "admin",
    "coordinator", "officer", "lead", "leader", "senior", "junior", "principal",
    "director", "head", "expert", "professional", "associate", "assistant",
    "sachbearbeiter", "mitarbeiter", "referent", "clerk", "staff", "team",
    "developer", "architect", "advisor", "generalist", "partner", "executive",
}


def _domain_anchor(attempts: List[SearchAttempt]) -> List[str]:
    """The words that make the role THIS role, taken from the FIRST attempt.

    The initial titles are the Strategist's read of the job before any relaxation,
    so they are the only uncontaminated statement of the domain we have. Generic
    role suffixes are stripped: they match every profession and would let any
    drift through.
    """
    if not attempts:
        return []
    initial = attempts[0].filters.get("currentJobTitles") or []
    anchor: set[str] = set()
    for title in initial:
        anchor |= {t for t in prescreen.tokens(title) if t not in _GENERIC_ROLE_WORDS}
    return sorted(anchor)


def _enforce_domain(
    decision: BroadenDecision, attempts: List[SearchAttempt],
) -> Optional[BroadenDecision]:
    """Strip proposed titles that abandoned the role's domain.

    The prompt tells the model to widen the net, not change the target — but a
    model told "the titles are too specific" will happily relax a payroll clerk
    into an "SAP Consultant", and the actor ORs the titles, so ONE off-domain
    entry is enough to flood the results with the wrong profession. Enforce it in
    code rather than trusting the instruction.

    Returns the decision with off-domain titles removed, or None to fall back to
    the Strategist's planned ladder when nothing on-domain survives.
    """
    anchor = _domain_anchor(attempts)
    if not anchor:
        return decision  # nothing to anchor to — don't invent a constraint
    proposed = decision.filters.currentJobTitles or []
    if not proposed:
        return decision

    kept, dropped = [], []
    for title in proposed:
        toks = prescreen.tokens(title)
        (kept if any(prescreen.token_present(a, toks) for a in anchor) else dropped).append(title)

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


def _history_prompt(brief: SearchBrief, attempts: List[SearchAttempt]) -> str:
    payload = brief.model_dump(exclude_defaults=True)
    payload.pop("jobDescription", None)  # the titles carry the signal by now
    lines = [
        f"Job brief:\n{json.dumps(payload, ensure_ascii=False, indent=2)}",
    ]
    # Name the domain explicitly. The brief's must-haves are what the candidate
    # will actually be scored against, so they define what may never be relaxed.
    anchor = _domain_anchor(attempts)
    if anchor:
        lines.append(
            "\nDOMAIN — every title you propose must still be about this. These are "
            "the words the role's original titles were built from, and at least one "
            "of them (or an obvious inflection) must appear in each title you "
            f"propose:\n  {', '.join(anchor)}"
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
) -> Optional[BroadenDecision]:
    """Decide the next broadened search, or None to stop.

    Returns None when the agent says stop, when no LLM is configured and the
    ladder is exhausted, or when the proposal is unusable.
    """
    ladder = ladder or []
    # attempts includes the initial search, so the 1st retry is attempt len(...).
    retry_number = len(attempts)

    if not llm_available():
        logger.info("[Broadener] no LLM key — using planned ladder step %d", retry_number)
        return _ladder_fallback(ladder, retry_number)

    try:
        result = await _build_agent().run(
            _history_prompt(brief, attempts),
            usage_limits=UsageLimits(request_limit=3),
        )
        decision = result.output
    except Exception as exc:  # noqa: BLE001 — degrade to the planned ladder
        logger.error("[Broadener] failed (%s) — planned ladder step %d",
                     exc, retry_number, exc_info=True)
        return _ladder_fallback(ladder, retry_number)

    if decision.decision == "give_up":
        logger.info("[Broadener] stopping: %s", decision.reasoning)
        return None
    if decision.filters.is_empty():
        logger.warning("[Broadener] proposed an empty filter set — planned ladder")
        return _ladder_fallback(ladder, retry_number)
    if _already_tried(decision.filters, attempts):
        logger.warning("[Broadener] proposed a filter set already tried — planned ladder")
        return _ladder_fallback(ladder, retry_number)

    # Widening must not become "search for a different job" — see _enforce_domain.
    guarded = _enforce_domain(decision, attempts)
    if guarded is None:
        return _ladder_fallback(ladder, retry_number)
    if guarded.filters.is_empty() or _already_tried(guarded.filters, attempts):
        # Stripping the drift can leave a set we've already run.
        return _ladder_fallback(ladder, retry_number)
    return guarded


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
