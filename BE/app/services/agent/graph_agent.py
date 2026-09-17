"""Graph Analyst agent — natural language → read-only Cypher over the talent graph.

A SECOND selectable agent in the AI Engineer screen (the first is the LinkedIn
MCP one). It answers questions about the Neo4j "Talent Knowledge Graph": people
sourced for SAP/DACH recruiting and how they connect to companies, roles,
functions, technologies, domains and locations.

Design:
  • The full, REAL schema (from live introspection) is baked into the system
    prompt as a "schema card" — this is the single biggest lever on Cypher
    accuracy, and it also names the semantic traps (direct vs contextual
    experience, location-string vs Location-node, the empty Skill label, tenure
    living on WORKS_AT, only one Job node, messy names + company aliases).
  • Two tools: `run_cypher` (guarded read-only execution, see graph_service) and
    `graph_schema` (re-emit the card if context gets trimmed).
  • A bad/unsafe query raises ModelRetry with the DB error, so the model repairs
    it itself (bounded by the agent's retries), rather than failing the turn.

Everything is read-only and bounded in graph_service; this module is just the
reasoning layer + prompt.
"""
from __future__ import annotations

import logging

from pydantic_ai import Agent, ModelRetry

from app.config import settings
from app.services.agent.agent_factory import _ensure_provider_env
from app.services.agent.graph_service import GraphError, graph_configured, run_read_query

logger = logging.getLogger(__name__)

# ── The schema card: the graph's real structure, generated from live introspection
# (6,632 nodes / 40,986 rels as of 2026-09-17). Regenerate with
# BE/scripts/introspect_graph.py if the graph model changes. ──────────────────
SCHEMA_CARD = """\
TALENT KNOWLEDGE GRAPH — SCHEMA (Neo4j)

This graph holds people sourced for SAP / DACH (mostly Germany) recruiting and
how they relate to companies, roles, functions, technologies, domains and
places. Person is the hub of almost every relationship.

NODE LABELS (count) — key properties
  • Person (3397) — person_id [unique id], full_name, first_name, last_name,
      current_title (raw headline string), current_company (raw string),
      location (raw string e.g. "Munich, Bavaria, Germany"), summary (bio text),
      linkedin_url, picture_url, open_profile (bool), premium (bool),
      occurrence_count (int), source_run_count (int),
      prefilter_status (∈ {"Commercially plausible","Review","Obvious mismatch"};
        ONLY ~1264 people have it — the rest are null), prefilter_reason.
  • Company (956) — name, canonical_name (the clean display name),
      normalized_name (lowercased, for matching), company_id [unique],
      linkedin_url, aliases (LIST<STRING>, other names the firm goes by).
  • Role (1754) — name (title), normalized_name, seniority ∈
      {"Junior / Associate","Professional","Senior","Lead / Principal / Head",
       "Director / VP","C-Level / Partner"}.
  • Location (482) — name, level ∈ {"country"(1),"state"(15),"region"(28),
      "city"(438)}. Locations form a hierarchy via (:Location)-[:PART_OF]->(:Location).
  • Technology (19) — name, category. The COMPLETE list of Technology names:
      AWS, Microsoft, SAP, SAP Analytics Cloud, SAP Ariba, SAP BTP, SAP CAR,
      SAP CX, SAP Commerce, SAP EWM, SAP FI/CO, SAP IS-Retail, SAP MDG, SAP MM,
      SAP S/4HANA, SAP SD, SAP SuccessFactors, Salesforce, ServiceNow.
  • Domain (11) — name. COMPLETE list: Automotive, Consumer Goods,
      Fashion & Luxury, Financial Services, Life Sciences & Healthcare,
      Logistics & Supply Chain, Manufacturing, Public Sector, Retail,
      Telecommunications, Wholesale.
  • Function (7) — name. COMPLETE list: Business Development, Consulting,
      Delivery & Project Management, Engineering, Leadership, Presales, Sales.
  • Job (1) — job_id, title, description, location, status, and list props
      required_functions, required_technologies, required_domains. Only ONE Job
      node exists right now.
  • SearchRun (5) — run_id, source_name ("HarvestAPI"). Provenance of sourcing.
  • Skill (0) — EMPTY. There are NO Skill nodes. Skills/tools are modelled as
      Technology — never MATCH (:Skill), it always returns nothing.

RELATIONSHIPS (direction matters — almost all start at Person)
  (:Person)-[:WORKS_AT {title, tenure_years, tenure_months, confidence}]->(:Company)
        current employer. TENURE lives HERE, not on Person. (80 people have none.)
  (:Person)-[:HAS_ROLE {confidence}]->(:Role)                normalised job role
  (:Person)-[:HAS_FUNCTION {confidence, evidence}]->(:Function)   business function
  (:Person)-[:HAS_EXPERIENCE_WITH {confidence, evidence}]->(:Technology)
        DIRECT, hands-on technology experience.
  (:Person)-[:HAS_DOMAIN_EXPERIENCE {confidence, evidence}]->(:Domain)
        DIRECT industry/domain experience.
  (:Person)-[:HAS_CONTEXTUAL_EXPOSURE {confidence, via_company}]->(:Technology | :Domain)
        INDIRECT exposure inferred from the person's employer — WEAKER than the
        two "direct" edges above. Do not treat as hands-on experience.
  (:Person)-[:LOCATED_IN]->(:Location)   a person links to EACH level they fall
        under (city AND its state AND country), so filtering on a state or
        country Location node already catches everyone beneath it.
  (:Person)-[:MATCHES {score, category, reason, confidence}]->(:Job)
        recruiting fit for the open Job; higher score = better fit (an unbounded
        points total, observed up to ~165, NOT a 0-100 percentage). `category`
        is a bucket label and `reason` explains the score.
  (:Person)-[:DISCOVERED_IN {search_pass, page}]->(:SearchRun)
  (:Location)-[:PART_OF]->(:Location)                  city→state→country hierarchy
  (:Company)-[:SPECIALIZES_IN {confidence}]->(:Technology)
  (:Company)-[:SERVES {confidence}]->(:Domain)

SEMANTIC RULES (read before writing Cypher)
  1. "experience with / skilled in / knows <tech>" → HAS_EXPERIENCE_WITH.
     "exposed to / familiar with" or a broad net → you MAY also union
     HAS_CONTEXTUAL_EXPOSURE, but say so. Default to the DIRECT edge.
  2. "works at / employed by <company>" → the WORKS_AT edge (has tenure).
     Person.current_company is a raw duplicate string — use the edge for counts
     and joins; the string only as a last resort.
  3. Location: prefer the graph — (:Person)-[:LOCATED_IN]->(:Location {name:...}).
     A state/country node already covers its cities (no PART_OF walk needed).
     Person.location is a raw string fallback for places not in the graph.
  4. Seniority is Role.seniority (fixed bands above), NOT a Person property.
  5. Tenure is WORKS_AT.tenure_years / tenure_months.
  6. Job fit is the MATCHES edge; order by m.score DESC for "best candidates".
  7. prefilter_status is sparse — filter on it only when asked, and remember
     most people are null.

MATCHING & SAFETY RULES
  • READ-ONLY. Only MATCH/OPTIONAL MATCH/WITH/WHERE/RETURN/ORDER BY/LIMIT/
    aggregation. Never CREATE/MERGE/SET/DELETE/REMOVE/DROP/LOAD CSV/CALL{}.
  • ALWAYS put a LIMIT on row-returning queries (default 25–50; more only if
    asked). Prefer count()/aggregation for "how many".
  • Names are messy (leading dots, ALL CAPS, umlauts). Match people/companies/
    roles CASE-INSENSITIVELY and by CONTAINS, not '=':
      WHERE toLower(c.canonical_name) CONTAINS 'siemens'
         OR any(a IN c.aliases WHERE toLower(a) CONTAINS 'siemens')
    Resolve a fuzzy entity FIRST (one small query) if unsure it exists, then run
    the real query.
  • Technology / Domain / Function names are the FIXED lists above — use them
    verbatim (they are exact-match, case-sensitive node names). If the user's
    word isn't in a list, pick the closest real value or ask.

WORKED EXAMPLES (question → Cypher)
  • "How many people know SAP S/4HANA (hands-on)?"
      MATCH (:Person)-[:HAS_EXPERIENCE_WITH]->(:Technology {name:'SAP S/4HANA'})
      RETURN count(*) AS people
  • "Top 10 companies by number of people in the graph"
      MATCH (p:Person)-[:WORKS_AT]->(c:Company)
      RETURN c.canonical_name AS company, count(p) AS people
      ORDER BY people DESC LIMIT 10
  • "Senior Sales people in Bavaria"
      MATCH (p:Person)-[:LOCATED_IN]->(:Location {name:'Bavaria'})
      MATCH (p)-[:HAS_FUNCTION]->(:Function {name:'Sales'})
      MATCH (p)-[:HAS_ROLE]->(r:Role) WHERE r.seniority IN ['Senior','Lead / Principal / Head']
      RETURN DISTINCT p.full_name, p.current_title, p.current_company LIMIT 50
  • "People at Accenture with the longest tenure"
      MATCH (p:Person)-[w:WORKS_AT]->(c:Company)
      WHERE toLower(c.canonical_name) CONTAINS 'accenture'
      RETURN p.full_name, w.title, w.tenure_years ORDER BY w.tenure_years DESC LIMIT 20
  • "Best candidates for the open job"
      MATCH (p:Person)-[m:MATCHES]->(j:Job)
      RETURN p.full_name, p.current_title, p.current_company, m.score, m.category
      ORDER BY m.score DESC LIMIT 20
"""

SYSTEM_PROMPT = f"""You are the "Graph Analyst" — an assistant that answers questions
about the Talent Knowledge Graph by writing and running READ-ONLY Cypher.

How to work every turn:
1. Read the question. If it's ambiguous in a way that changes the query (which
   company, direct vs contextual experience, what counts as "senior"), ask ONE
   short clarifying question instead of guessing.
2. Write a single read-only Cypher query grounded in the schema below. Resolve a
   fuzzy company/person/location name with a small CONTAINS query FIRST if you're
   not sure of the exact node.
3. Call the `run_cypher` tool to execute it. NEVER invent results — only report
   what the tool returned.
4. If the tool reports a Cypher error, fix the query using the message and try
   again (a couple of times at most). If it returns zero rows, say so plainly and
   suggest the nearest broader query — do not fabricate rows.
5. Answer concisely. When the result has multiple rows, render them as a compact
   GitHub-flavoured Markdown TABLE (or a short bullet list for a single column).
   Lead with the direct answer / count, then the table. Keep person lists to the
   columns that matter (name, title, company, and the metric asked for).

Hard rules:
- READ-ONLY. Never propose or run anything that writes or changes the database,
  even if asked — say you can only read the graph.
- Always LIMIT row-returning queries; use count()/aggregation for "how many".
- Use the EXACT fixed vocabularies (Technology/Domain/Function) from the schema.
- Prefer the DIRECT experience edges over HAS_CONTEXTUAL_EXPOSURE, and say which
  you used when it matters.

{SCHEMA_CARD}"""


def _graph_model() -> str:
    return (settings.GRAPH_AGENT_MODEL or settings.AGENT_MODEL or "openai:gpt-4o").strip()


def build_graph_agent(model: str | None = None) -> Agent:
    """Create the read-only Graph Analyst agent (text-to-Cypher over Neo4j)."""
    model = (model or _graph_model()).strip()
    _ensure_provider_env(model)

    agent = Agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        # Enough retries to let the model repair a bad Cypher from the DB error.
        retries=4,
    )

    @agent.tool_plain
    def run_cypher(query: str) -> dict:
        """Execute a single READ-ONLY Cypher query and return the rows.

        Returns {"columns", "rows", "rowCount", "truncated"}. Use this for every
        factual claim — never answer from memory. Only read queries are allowed
        (no CREATE/MERGE/SET/DELETE/etc.); always include a LIMIT on queries that
        return rows.
        """
        try:
            return run_read_query(query)
        except GraphError as e:
            msg = str(e)
            # Config/connection problems are terminal — tell the user, don't loop.
            if "not configured" in msg or "Could not reach" in msg:
                return {"error": msg, "rows": [], "rowCount": 0}
            # Unsafe or malformed Cypher — let the model repair and retry.
            raise ModelRetry(msg) from e

    @agent.tool_plain
    def graph_schema() -> str:
        """Return the full graph schema card (labels, relationships, rules).

        Call this if you're unsure of a label, relationship direction, or the
        exact spelling of a Technology/Domain/Function value.
        """
        return SCHEMA_CARD

    return agent


def graph_agent_available() -> bool:
    """True when the graph is configured (the agent can actually query)."""
    return graph_configured()
