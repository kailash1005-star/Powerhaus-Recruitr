"""Safety and registry coverage for the read-only Talent Graph agent."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.v1.agent import CreateThreadBody, StreamBody
from app.config import settings
from app.services.agent import agent_registry
from app.services.agent.graph_service import GraphError, assert_read_only


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) RETURN n LIMIT 1",
        "MATCH (p:Person) RETURN count(p) AS people",
        "RETURN 'DELETE CREATE CALL' AS harmless_literal",
        "// DELETE is mentioned in a comment\nMATCH (n) RETURN n LIMIT 1",
    ],
)
def test_safe_reads_are_allowed(query):
    assert_read_only(query)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) DELETE n RETURN n",
        "CREATE (n:Person) RETURN n",
        "MERGE (n:Person {person_id: 'x'}) RETURN n",
        "MATCH (n) SET n.changed = true RETURN n",
        "CALL apoc.create.node(['Person'], {}) YIELD node RETURN node",
        "CALL apoc.cypher.runWrite('CREATE (n)', {}) YIELD value RETURN value",
        "CALL { MATCH (n) RETURN n } RETURN n",
        "SHOW USERS",
        "MATCH (n) RETURN n; MATCH (m) RETURN m",
    ],
)
def test_writes_procedures_admin_and_multiple_statements_are_blocked(query):
    with pytest.raises(GraphError):
        assert_read_only(query)


def test_query_must_return_results():
    with pytest.raises(GraphError, match="RETURN"):
        assert_read_only("MATCH (n)")


def test_registry_resolves_known_agents():
    assert agent_registry.resolve_agent("engineer").id == "engineer"
    assert agent_registry.resolve_agent("graph").id == "graph"
    assert agent_registry.resolve_agent(None).id == "engineer"


def test_registry_rejects_unknown_agent():
    with pytest.raises(ValueError, match="Unknown agent"):
        agent_registry.resolve_agent("surprise-agent")


def test_agent_list_reports_graph_availability(monkeypatch):
    monkeypatch.setattr(settings, "NEO4J_URI", "")
    monkeypatch.setattr(settings, "NEO4J_PASSWORD", "")
    agents = {item["id"]: item for item in agent_registry.list_agents()}
    assert agents["engineer"]["available"] is True
    assert agents["graph"]["available"] is False


def test_api_rejects_unknown_agent_id():
    with pytest.raises(ValidationError):
        CreateThreadBody(agent="surprise-agent")


def test_stream_cannot_override_thread_agent():
    with pytest.raises(ValidationError):
        StreamBody(message="hello", agent="graph")
