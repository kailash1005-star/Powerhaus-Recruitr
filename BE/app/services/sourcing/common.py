"""Shared setup for the sourcing agents: model resolution + provider keys.

Mirrors `services/agent/agent_factory.py`'s provider-env pattern so a model
string like "anthropic:claude-sonnet-4-6" works here exactly as it does for the
AI Engineer agent — Pydantic AI reads os.environ directly, but our keys arrive
via pydantic-settings, so they have to be bridged.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.services.agent.agent_factory import _ensure_provider_env

logger = logging.getLogger(__name__)


def get_model(tier: str = "smart") -> str:
    """Resolve a tier to a Pydantic AI model string, ensuring its key is in env.

    tier='smart' → the Strategist's one-shot reasoning call per prefill.
    tier='fast'  → the Broadener, called once per zero-result retry.
    """
    model = (
        settings.SOURCING_STRATEGY_MODEL if tier == "smart"
        else settings.SOURCING_BROADEN_MODEL
    ).strip()
    _ensure_provider_env(model)
    return model


def llm_available() -> bool:
    """Whether any provider key is configured.

    The sourcing agents are an enhancement, never a hard dependency: with no key
    the caller falls back to the old literal-title prefill instead of erroring.
    """
    return bool(
        settings.OPENAI_API_KEY or settings.ANTHROPIC_API_KEY
        or settings.GEMINI_API_KEY or settings.OPENROUTER_API_KEY
    )
