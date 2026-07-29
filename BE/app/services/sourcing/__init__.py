"""Agentic candidate sourcing.

Two reasoning-only Pydantic AI agents wrap the existing Apify LinkedIn search:

  • `strategist` — job title + JD + recruiter hints → the filters real profiles
    actually match, with per-field rationale and a broadening ladder. Runs once,
    on prefill, before any money is spent.
  • `judge` — an LLM-as-judge that runs right after the Strategist, still before
    any money is spent, to verify the proposed titles + Boolean query would
    actually return real LinkedIn/Apify profiles, and repairs them when they
    wouldn't.
  • `broadener` — runs only after a search returns zero, relaxing the filters for
    the next attempt based on what already failed.

All degrade gracefully: no API key, or a failed call, falls back to a cleaned
literal-title prefill rather than blocking the recruiter.
"""
from app.services.sourcing.brief import build_brief
from app.services.sourcing.broadener import next_attempt
from app.services.sourcing.judge import judge_query
from app.services.sourcing.models import (
    ApolloPlan, BroadenDecision, BroadeningStep, QueryJudgment, SearchAttempt,
    SearchBrief, SearchFilters, SearchStrategy,
)
from app.services.sourcing.strategist import propose_strategy

__all__ = [
    "build_brief",
    "judge_query",
    "next_attempt",
    "propose_strategy",
    "ApolloPlan",
    "BroadenDecision",
    "BroadeningStep",
    "QueryJudgment",
    "SearchAttempt",
    "SearchBrief",
    "SearchFilters",
    "SearchStrategy",
]
