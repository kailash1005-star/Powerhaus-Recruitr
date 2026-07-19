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


# ── Two-tier domain anchor (shared by Strategist sanitize + Broadener guard) ──

# Platform/vendor brands that span MANY professions. A title matching one of
# these alone proves nothing about the specialization: "SAP" is carried by
# FICO consultants, Basis admins and HCM consultants alike. Kept deliberately
# conservative — a specialty word wrongly listed here would weaken the anchor,
# while a missing brand merely falls back to the LLM-declared anchor.
ECOSYSTEM_TOKENS = frozenset({
    "sap", "oracle", "microsoft", "aws", "azure", "gcp", "google", "salesforce",
    "servicenow", "ibm", "adobe", "cisco", "vmware", "atlassian", "dynamics",
    "netsuite", "s4hana", "hana",
})

# Seniority/form words shared by every profession — they can never evidence
# that a title is still the same job. (Superset shared with the Broadener.)
GENERIC_ROLE_WORDS = frozenset({
    "consultant", "consulting", "berater", "beratung", "specialist", "spezialist",
    "manager", "management", "engineer", "analyst", "administrator", "admin",
    "coordinator", "officer", "lead", "leader", "senior", "junior", "principal",
    "director", "head", "expert", "professional", "associate", "assistant",
    "sachbearbeiter", "mitarbeiter", "referent", "clerk", "staff", "team",
    "developer", "architect", "advisor", "generalist", "partner", "executive",
})


def derive_anchor_terms(sources: list[str]) -> tuple[list[str], list[str]]:
    """Heuristic (coreTerms, ecosystemTerms) from titles/queries.

    Used when no LLM-declared anchor is available. Tokens are split into
    ecosystem brands vs everything else; the "everything else" minus generic
    role words is the core. When the ONLY signal is an ecosystem brand (a
    "Workday Consultant"-style single-token domain), that brand IS the core —
    the ecosystem rule only demotes a brand when a more specific word exists.
    """
    from app.services import prescreen_service as prescreen

    core: set[str] = set()
    eco: set[str] = set()
    for source in sources:
        for tok in prescreen.tokens(source or ""):
            if tok in GENERIC_ROLE_WORDS:
                continue
            (eco if tok in ECOSYSTEM_TOKENS else core).add(tok)
    if not core and eco:
        # Brand-only domain: the brand is the specialization.
        return sorted(eco), []
    return sorted(core), sorted(eco)


def title_in_domain(title: str, core_terms: list[str]) -> bool:
    """True when the title carries at least one CORE term (fuzzy, inflection-
    tolerant). Ecosystem terms deliberately don't count — see DomainAnchor."""
    from app.services import prescreen_service as prescreen

    if not core_terms:
        return True  # nothing to anchor to — don't invent a constraint
    toks = prescreen.tokens(title)
    return any(prescreen.token_present(c, toks) for c in core_terms)
