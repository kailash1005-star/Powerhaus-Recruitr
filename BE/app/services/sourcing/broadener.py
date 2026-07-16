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

Diagnose before you widen. The history tells you what to blame:
  • Very specific titles tried and all zero → the titles are the problem.
    Generalise them ("SAP FICO Consultant" → "SAP Consultant", then "ERP
    Consultant"). Add adjacent roles people actually move between.
  • A narrow city tried and zero → widen to the metro, then the country. Talent
    for most roles clusters in a few metros, not the town the office is in.
  • Several enum filters set → drop the most speculative one. seniorityLevel and
    yearsOfExperience are the usual culprits: they are inferred, and members
    often leave the underlying profile data blank, so filtering on them silently
    excludes people who DO qualify.
  • currentCompanies set → this restricts to a handful of employers. Drop it
    unless the recruiter explicitly asked for a poach from those companies.

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


def _history_prompt(brief: SearchBrief, attempts: List[SearchAttempt]) -> str:
    payload = brief.model_dump(exclude_defaults=True)
    payload.pop("jobDescription", None)  # the titles carry the signal by now
    lines = [
        f"Job brief:\n{json.dumps(payload, ensure_ascii=False, indent=2)}",
        "\nAttempts so far (all returned zero):",
    ]
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
