"""Read-only access to the Talent Knowledge Graph (Neo4j).

This is the ONLY place the Graph Analyst agent touches the database, and it is
read-only by construction — three independent guards, because a text-to-Cypher
agent will eventually emit something it shouldn't:

  1. Static guard (`assert_read_only`): the query is stripped of strings/comments
     and rejected if it contains any write clause, procedure call, administrative
     command, or more than one statement. Agent queries never need CALL/SHOW;
     blocking them closes the custom/APOC write-procedure escape hatch.
  2. Server-side READ access mode: the query runs in a READ transaction, so the
     server itself refuses any write even if the static guard is somehow bypassed.
  3. Bounds: a per-query transaction timeout and a hard row cap, so a heavy or
     unbounded query can't hang the turn or dump the whole graph into the model's
     context.

Degrades gracefully: with no `NEO4J_URI` configured, `graph_configured()` is
False and the agent tells the user the graph isn't connected instead of erroring.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Optional

import neo4j
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from app.config import settings

logger = logging.getLogger(__name__)

# Write / schema / admin clauses that must never appear in a generated query.
# Matched as whole words, case-insensitively, AFTER strings + comments are
# stripped (so a literal like "'DELETE me'" can't trip it).
_FORBIDDEN = (
    "CREATE", "MERGE", "DELETE", "SET", "REMOVE", "DROP", "FOREACH",
    "LOAD", "CALL", "SHOW",
    "GRANT", "DENY", "REVOKE", "START", "STOP", "TERMINATE", "USE",
)
# apoc procedures that mutate — the read guard also blocks the obvious ones.
_FORBIDDEN_PROC = re.compile(
    r"\bapoc\.(?:create|merge|refactor|periodic|trigger|schema|do)\b"
    r"|\bdb\.(?:create|drop|await)\b"
    r"|\bdbms\.(?:security|setConfigValue)\b",
    re.IGNORECASE,
)
_STRING_LIT = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class GraphError(Exception):
    """A user-facing graph problem (unsafe query, connection, or Cypher error)."""


def graph_configured() -> bool:
    """True when a Neo4j endpoint is configured (agent is usable)."""
    return bool(settings.NEO4J_URI and settings.NEO4J_PASSWORD)


@lru_cache(maxsize=1)
def get_driver() -> "neo4j.Driver":
    """Lazily build a single shared driver (connection pool).

    lru_cache makes this a process-wide singleton; `close_driver()` clears it.
    """
    if not graph_configured():
        raise GraphError("The talent graph is not configured (NEO4J_URI is unset).")
    driver = GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        # Fail a dead/slow endpoint fast rather than hanging the chat turn.
        connection_acquisition_timeout=15,
        max_connection_lifetime=1800,
    )
    return driver


def close_driver() -> None:
    """Close the shared driver (call on app shutdown)."""
    if get_driver.cache_info().currsize:  # a driver was actually built
        try:
            get_driver().close()
        except Exception:  # noqa: BLE001
            pass
        finally:
            get_driver.cache_clear()


def _strip_literals(cypher: str) -> str:
    """Remove string literals and comments so keyword scanning sees only code."""
    s = _BLOCK_COMMENT.sub(" ", cypher)
    s = _LINE_COMMENT.sub(" ", s)
    s = _STRING_LIT.sub("''", s)
    return s


def assert_read_only(cypher: str) -> None:
    """Raise GraphError if `cypher` is not a single read-only statement.

    This is guard #1. It is intentionally strict: false positives are safe (the
    user just rephrases), whereas a missed write is not.
    """
    if not cypher or not cypher.strip():
        raise GraphError("Empty query.")

    bare = _strip_literals(cypher)

    # Single statement only. A stray trailing ';' is fine; an embedded one is not.
    if ";" in bare.rstrip().rstrip(";"):
        raise GraphError("Only a single Cypher statement is allowed (no ';').")

    # Whole-word write/procedure/admin clause scan.
    words = {w.upper() for w in _WORD.findall(bare)}
    hit = words.intersection(_FORBIDDEN)
    if hit:
        raise GraphError(
            f"Write/DDL keyword(s) not allowed in read-only mode: {', '.join(sorted(hit))}."
        )
    if _FORBIDDEN_PROC.search(bare):
        raise GraphError("That procedure can modify data and is not allowed.")

    # Must actually be a query that returns something.
    if not re.search(r"\bRETURN\b", bare, re.IGNORECASE):
        raise GraphError("Query must RETURN results (read-only).")


def _jsonify(value: Any) -> Any:
    """Make a Neo4j value JSON-serialisable for the model.

    Nodes/relationships collapse to their property maps; temporal/spatial types
    become strings; containers recurse.
    """
    if isinstance(value, (neo4j.graph.Node, neo4j.graph.Relationship)):
        return {**dict(value)}
    if isinstance(value, neo4j.graph.Path):
        return {"nodes": [_jsonify(n) for n in value.nodes],
                "relationships": [_jsonify(r) for r in value.relationships]}
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    # neo4j.time.* (Date/DateTime/Duration) and spatial Point → str.
    if value.__class__.__module__.startswith(("neo4j.time", "neo4j.spatial")):
        return str(value)
    return value


def run_read_query(cypher: str, params: Optional[dict] = None) -> dict:
    """Run a guarded, read-only, bounded Cypher query. Returns a result dict.

    Shape: {"columns": [...], "rows": [ {col: val, ...}, ... ],
            "rowCount": int, "truncated": bool}. Raises GraphError on an unsafe
    query, a connection failure, or a Cypher error (with the DB message, so the
    agent can repair and retry).
    """
    assert_read_only(cypher)  # guard #1 — may raise before we ever connect
    if not graph_configured():
        raise GraphError("The talent graph is not configured.")

    max_rows = max(1, int(settings.GRAPH_QUERY_MAX_ROWS))
    timeout = max(1, int(settings.GRAPH_QUERY_TIMEOUT_S))
    try:
        driver = get_driver()
        # guard #2 — a READ transaction: the server refuses writes regardless.
        with driver.session(
            database=settings.NEO4J_DATABASE or "neo4j",
            default_access_mode=neo4j.READ_ACCESS,
        ) as session:
            # Driver expects the timeout as a number of seconds (not a timedelta).
            tx = session.begin_transaction(timeout=timeout)
            try:
                result = tx.run(cypher, params or {})
                columns = list(result.keys())
                rows: list[dict] = []
                truncated = False
                for i, record in enumerate(result):
                    if i >= max_rows:  # guard #3 — hard row cap
                        truncated = True
                        break
                    rows.append({k: _jsonify(v) for k, v in record.items()})
            finally:
                tx.rollback()  # read-only: nothing to commit
    except GraphError:
        raise
    except Neo4jError as e:
        # A syntax/semantic Cypher error — surface the message so the agent can fix it.
        raise GraphError(f"Cypher error [{e.code}]: {e.message}") from e
    except Exception as e:  # noqa: BLE001 — connection/timeout/etc.
        logger.error("[GraphService] query failed: %s", e, exc_info=True)
        raise GraphError(f"Could not reach the talent graph: {type(e).__name__}: {e}") from e

    return {"columns": columns, "rows": rows, "rowCount": len(rows), "truncated": truncated}
