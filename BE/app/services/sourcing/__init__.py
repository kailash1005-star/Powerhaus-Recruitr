"""Agentic candidate sourcing.

Two reasoning-only Pydantic AI agents wrap the existing Apify LinkedIn search:

  • `strategist` — job title + JD + recruiter hints → the filters real profiles
    actually match, with per-field rationale and a broadening ladder. Runs once,
    on prefill, before any money is spent.
  • `broadener` — runs only after a search returns zero, relaxing the filters for
    the next attempt based on what already failed.

Both degrade gracefully: no API key, or a failed call, falls back to the previous
literal-title behaviour rather than blocking the recruiter.
"""
from app.services.sourcing.brief import build_brief
from app.services.sourcing.broadener import next_attempt
from app.services.sourcing.models import (
    BroadenDecision, BroadeningStep, SearchAttempt, SearchBrief, SearchFilters,
    SearchStrategy,
)
from app.services.sourcing.strategist import propose_strategy

__all__ = [
    "build_brief",
    "next_attempt",
    "propose_strategy",
    "BroadenDecision",
    "BroadeningStep",
    "SearchAttempt",
    "SearchBrief",
    "SearchFilters",
    "SearchStrategy",
]
