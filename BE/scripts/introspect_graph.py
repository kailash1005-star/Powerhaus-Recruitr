"""Read-only introspection of the Talent Knowledge Graph (Neo4j).

Purpose
-------
Dump the LIVE schema of the graph so the "Graph Analyst" agent can be built on
the ACTUAL structure (labels, relationship types, property shapes, connectivity
patterns, sample values) instead of guesses. Runs ONLY read queries — no writes,
ever.

Usage
-----
Set the connection via env vars (defaults target a local Neo4j), then run it from
the BE/ directory:

    # PowerShell
    $env:NEO4J_URI="neo4j://localhost:7687"; $env:NEO4J_USER="neo4j"; $env:NEO4J_PASSWORD="password123"
    python scripts/introspect_graph.py

    # bash
    NEO4J_URI=neo4j://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=password123 \
        python scripts/introspect_graph.py

It writes ``neo4j_schema.json`` next to where you run it and prints a summary.
Send that JSON back so the agent's schema card / system prompt can be generated
from the real graph.

Notes
-----
* Prefer a direct bolt URI (``neo4j://localhost:7687`` on the machine that hosts
  the DB). A cloudflare *quick* tunnel (``*.trycloudflare.com``) proxies HTTP,
  not raw Bolt, and its hostname rotates every restart — don't rely on it.
* ``neo4j+s://`` = TLS + cert check, ``neo4j://`` = plaintext (local), ``bolt://``
  = single instance. Match whatever the DB actually exposes.
"""
from __future__ import annotations

import json
import os
import sys

from neo4j import GraphDatabase

URI = os.environ.get("NEO4J_URI", "neo4j://localhost:7687")
USER = os.environ.get("NEO4J_USER", "neo4j")
PASSWORD = os.environ.get("NEO4J_PASSWORD", "password123")
DB = os.environ.get("NEO4J_DATABASE", "neo4j")
# Cap the per-label sampling so a huge graph can't make this run for minutes.
MAX_LABELS_SAMPLED = int(os.environ.get("INTROSPECT_MAX_LABELS", "60"))

out: dict = {"connection": {"uri": URI, "database": DB}}


def run(session, cypher, **params):
    return [r.data() for r in session.run(cypher, **params)]


def main() -> None:
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    driver.verify_connectivity()
    with driver.session(database=DB) as s:
        out["totals"] = {
            "nodes": run(s, "MATCH (n) RETURN count(n) AS c")[0]["c"],
            "relationships": run(s, "MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"],
        }
        out["labels"] = [r["label"] for r in run(
            s, "CALL db.labels() YIELD label RETURN label ORDER BY label")]
        out["relationshipTypes"] = [r["relationshipType"] for r in run(
            s, "CALL db.relationshipTypes() YIELD relationshipType "
               "RETURN relationshipType ORDER BY relationshipType")]
        out["propertyKeys"] = [r["propertyKey"] for r in run(
            s, "CALL db.propertyKeys() YIELD propertyKey RETURN propertyKey ORDER BY propertyKey")]

        out["labelCounts"] = {
            lbl: run(s, f"MATCH (n:`{lbl}`) RETURN count(n) AS c")[0]["c"]
            for lbl in out["labels"]
        }

        # Property shape (names + types) per label and per rel type.
        try:
            out["nodeTypeProperties"] = run(
                s, "CALL db.schema.nodeTypeProperties() "
                   "YIELD nodeLabels, propertyName, propertyTypes, mandatory "
                   "RETURN nodeLabels, propertyName, propertyTypes, mandatory")
        except Exception as e:  # noqa: BLE001
            out["nodeTypeProperties_error"] = str(e)
        try:
            out["relTypeProperties"] = run(
                s, "CALL db.schema.relTypeProperties() "
                   "YIELD relType, propertyName, propertyTypes, mandatory "
                   "RETURN relType, propertyName, propertyTypes, mandatory")
        except Exception as e:  # noqa: BLE001
            out["relTypeProperties_error"] = str(e)

        # Connectivity: which (startLabels)-[REL]->(endLabels), with counts.
        out["relPatterns"] = run(
            s,
            "MATCH (a)-[r]->(b) "
            "WITH labels(a) AS startLabels, type(r) AS relType, labels(b) AS endLabels, count(*) AS count "
            "RETURN startLabels, relType, endLabels, count ORDER BY count DESC LIMIT 300")

        # Real sample rows + per-property distinct value samples (categorical hints).
        samples: dict = {}
        distinct: dict = {}
        for lbl in out["labels"][:MAX_LABELS_SAMPLED]:
            rows = run(s, f"MATCH (n:`{lbl}`) RETURN n LIMIT 3")
            samples[lbl] = [r["n"] for r in rows]
            keys = run(s, f"MATCH (n:`{lbl}`) WITH keys(n) AS ks LIMIT 500 "
                          "UNWIND ks AS k RETURN DISTINCT k ORDER BY k")
            distinct[lbl] = {}
            for k in [row["k"] for row in keys]:
                vals = run(
                    s,
                    f"MATCH (n:`{lbl}`) WHERE n.`{k}` IS NOT NULL "
                    f"RETURN DISTINCT n.`{k}` AS v LIMIT 12")
                distinct[lbl][k] = [row["v"] for row in vals]
        out["sampleNodes"] = samples
        out["distinctValueSamples"] = distinct

        for name, q in (
            ("constraints", "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
                            "RETURN name, type, labelsOrTypes, properties"),
            ("indexes", "SHOW INDEXES YIELD name, type, labelsOrTypes, properties "
                        "RETURN name, type, labelsOrTypes, properties"),
        ):
            try:
                out[name] = run(s, q)
            except Exception as e:  # noqa: BLE001
                out[f"{name}_error"] = str(e)

    driver.close()

    with open("neo4j_schema.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str, ensure_ascii=False)

    print("NODES:", out["totals"]["nodes"], " RELATIONSHIPS:", out["totals"]["relationships"])
    print("\nLABEL COUNTS:")
    for k, v in sorted(out["labelCounts"].items(), key=lambda x: -x[1]):
        print(f"  {k:30} {v}")
    print("\nRELATIONSHIP TYPES:", ", ".join(out["relationshipTypes"]))
    print("\nTOP CONNECTIVITY PATTERNS:")
    for p in out["relPatterns"][:40]:
        print(f"  ({'/'.join(p['startLabels'])})-[:{p['relType']}]->({'/'.join(p['endLabels'])})  x{p['count']}")
    print("\nWrote neo4j_schema.json — send this file back.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("INTROSPECTION FAILED:", type(e).__name__, e, file=sys.stderr)
        print("Check NEO4J_URI/USER/PASSWORD and that the DB is reachable from here.", file=sys.stderr)
        sys.exit(1)
