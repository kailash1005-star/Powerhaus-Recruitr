"""Build the provider-swappable Pydantic AI agent + its MCP toolsets.

Model swapping is a one-string change (settings.AGENT_MODEL or a per-request
override), e.g. "openai:gpt-4o" → "anthropic:claude-sonnet-4-6". The agent's
tools come from connected MCP server(s); add more by appending to
build_mcp_toolsets().
"""
from __future__ import annotations

import logging
import os

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """\
You are the "AI Engineer" — an autonomous assistant embedded in Recruitr, a
recruitment SaaS. You accomplish tasks by CALLING the tools provided by connected
MCP servers (LinkedIn company/people/job search, enrichment, and more).

How to behave:
- When a request needs real data or an action, CALL the appropriate tool
  IMMEDIATELY, in this same turn. Do NOT reply with "I'll look that up",
  "please hold on", or any promise to act later — just call the tool now.
- Chain tools when needed (e.g. search, then fetch details on a result).
- After the tools return, answer using the ACTUAL data they returned — concise,
  concrete, and specific.
- Never invent data. If a tool returns an auth, rate-limit, or "not found" error,
  state that plainly instead of guessing.
- For actions that change something (sending messages, connection requests),
  confirm the user's intent before calling the tool.
"""

# Map a Pydantic AI provider prefix → the env var its provider reads, sourced
# from our settings. Lets a key in .env (loaded by pydantic-settings) reach
# Pydantic AI, which reads os.environ directly.
_PROVIDER_ENV = {
    "openai": ("OPENAI_API_KEY", lambda: settings.OPENAI_API_KEY),
    "anthropic": ("ANTHROPIC_API_KEY", lambda: settings.ANTHROPIC_API_KEY),
    "google-gla": ("GEMINI_API_KEY", lambda: settings.GEMINI_API_KEY),
    "google-vertex": ("GEMINI_API_KEY", lambda: settings.GEMINI_API_KEY),
    "openrouter": ("OPENROUTER_API_KEY", lambda: settings.OPENROUTER_API_KEY),
}


def _ensure_provider_env(model: str) -> None:
    """Populate os.environ with the provider key for `model` if we have it."""
    prefix = model.split(":", 1)[0]
    entry = _PROVIDER_ENV.get(prefix)
    if not entry:
        return
    env_name, getter = entry
    value = getter()
    if value and not os.environ.get(env_name):
        os.environ[env_name] = value


def build_mcp_toolsets() -> list:
    """Construct the MCP server toolsets the agent should connect to.

    Append more servers here as the recruiter's capabilities are exposed over MCP.
    """
    servers: list = []

    # LinkedIn MCP server — HTTP preferred (run it as a service), else stdio.
    if settings.AGENT_MCP_LINKEDIN_HTTP_URL:
        headers = (
            {"Authorization": f"Bearer {settings.AGENT_MCP_AUTH_TOKEN}"}
            if settings.AGENT_MCP_AUTH_TOKEN else None
        )
        servers.append(
            MCPServerStreamableHTTP(
                url=settings.AGENT_MCP_LINKEDIN_HTTP_URL,
                headers=headers,
                tool_prefix="linkedin",
            )
        )
        logger.info("Agent MCP: LinkedIn via HTTP %s", settings.AGENT_MCP_LINKEDIN_HTTP_URL)
    elif settings.AGENT_MCP_LINKEDIN_DIR:
        servers.append(
            MCPServerStdio(
                command="uv",
                args=["run", "--directory", settings.AGENT_MCP_LINKEDIN_DIR, "linkedin-mcp"],
                tool_prefix="linkedin",
            )
        )
        logger.info("Agent MCP: LinkedIn via stdio in %s", settings.AGENT_MCP_LINKEDIN_DIR)
    else:
        logger.info("Agent MCP: no LinkedIn server configured — running as plain chat.")

    return servers


def build_agent(model: str | None = None) -> Agent:
    """Create a Pydantic AI agent for the given model string (provider-swappable)."""
    model = (model or settings.AGENT_MODEL).strip()
    _ensure_provider_env(model)
    return Agent(
        model,
        system_prompt=(settings.AGENT_SYSTEM_PROMPT or DEFAULT_SYSTEM_PROMPT),
        toolsets=build_mcp_toolsets(),
    )
