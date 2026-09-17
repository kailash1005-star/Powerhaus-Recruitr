"""The set of selectable agents behind the AI Engineer screen.

Two today:
  • "engineer" — the LinkedIn MCP tool-calling assistant (agent_factory).
  • "graph"    — the read-only Graph Analyst over the Neo4j talent graph
                 (graph_agent).

Both are Pydantic AI agents driven by the same streaming runner. The only
behavioural difference the runner needs to know is whether an agent's tools come
from MCP servers (so it can degrade to plain chat if the MCP endpoint is down) —
the graph agent's tools are in-process, so that fallback doesn't apply.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from pydantic_ai import Agent

from app.services.agent.agent_factory import build_agent
from app.services.agent.graph_agent import build_graph_agent, graph_agent_available

DEFAULT_AGENT = "engineer"


@dataclass(frozen=True)
class AgentSpec:
    id: str
    label: str
    description: str
    # (model, with_tools) -> Agent
    builder: Callable[[Optional[str], bool], Agent]
    uses_mcp: bool  # True → runner may retry as plain chat if MCP is unreachable
    available: Callable[[], bool]


_REGISTRY: dict[str, AgentSpec] = {
    "engineer": AgentSpec(
        id="engineer",
        label="AI Engineer (LinkedIn)",
        description="Tool-calling assistant backed by the LinkedIn MCP server.",
        builder=lambda model, with_tools=True: build_agent(model, with_tools=with_tools),
        uses_mcp=True,
        available=lambda: True,
    ),
    "graph": AgentSpec(
        id="graph",
        label="Graph Analyst (Talent Graph)",
        description="Answers questions about the Neo4j talent graph with read-only Cypher.",
        # with_tools is irrelevant here — the graph tools are always in-process.
        builder=lambda model, with_tools=True: build_graph_agent(model),
        uses_mcp=False,
        available=graph_agent_available,
    ),
}


def resolve_agent(agent_id: Optional[str]) -> AgentSpec:
    """Return the AgentSpec for an id; reject unknown ids instead of misrouting."""
    resolved_id = (agent_id or "").strip() or DEFAULT_AGENT
    try:
        return _REGISTRY[resolved_id]
    except KeyError as exc:
        raise ValueError(f"Unknown agent: {resolved_id}") from exc


def list_agents() -> list[dict]:
    """Public agent list for the UI picker (id/label/description/available)."""
    return [
        {
            "id": s.id,
            "label": s.label,
            "description": s.description,
            "available": s.available(),
        }
        for s in _REGISTRY.values()
    ]
