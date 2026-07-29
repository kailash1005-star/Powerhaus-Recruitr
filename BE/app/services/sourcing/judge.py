"""Query Judge agent (fast model, reasoning only, no tools).

Runs ONCE per prefill, right after the Strategist and before any vendor spend.
It is an LLM-as-judge with a single job: decide whether the proposed job titles
and Boolean ``searchQuery`` will TECHNICALLY return real LinkedIn/Apify profiles,
or whether they are the kind of query that comes back empty — and, when they
won't work, hand back a repaired query/title family the pipeline swaps in.

Why a separate agent and not more Strategist prompt: the Strategist optimises for
translating a role into titles; it is a poor critic of its own output. A cheap,
adversarial second pass ("pretend you are LinkedIn search — would this match
anyone?") catches the failure shapes the Strategist is blind to: a verbatim
posting title, an over-constrained AND-block of skills, malformed Boolean syntax,
or non-title jargon in the query slot. The Judge NEVER changes the target
profession — it fixes phrasing so a real search runs, and the domain guard in
code still holds it to the specialization.

Cheap and bounded: one fast-model call, no tools, no vendor spend, and a
request_limit cap. Degrades to a no-op (verdict "searchable", no edits) whenever
no LLM key is configured or the call fails — the Strategist's output stands.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from pydantic_ai import Agent
from pydantic_ai.usage import UsageLimits

from app.services.sourcing.common import get_model, llm_available
from app.services.sourcing.models import QueryJudgment, SearchStrategy

logger = logging.getLogger(__name__)

INSTRUCTIONS = """You are a LinkedIn people-search QUALITY JUDGE. You do NOT write
searches — you stress-test one that has already been written, the way LinkedIn's
own search engine would run it, and you decide whether it will return REAL,
qualified profiles or come back empty.

You are given: the interpreted role, the target job titles, the Boolean
`searchQuery`, and the job description. Judge ONLY whether the titles + query are
technically searchable on LinkedIn/Apify. You are NOT judging whether the role is
a good hire, and you must NEVER change the target profession.

Verdict rubric:
  • "searchable" — a LinkedIn recruiter typing this would get real people. The
    titles are self-described headlines real members carry, and the query is a
    short OR-group of those titles.
  • "risky" — it will probably return people, but has a real weakness (one
    slightly employer-flavoured title, a query a touch long, a rare skill). Flag
    it; supply a fix only if it clearly helps.
  • "unsearchable" — it will almost certainly return ZERO. The failure shapes:
      - the query is the VERBATIM posting/job title (employer language nobody
        headlines themselves with, e.g. "Senior Inhouse Consultant SAP-CO/PS");
      - an over-constrained AND-block of skills/tools ("... AND (\"Windows Server\"
        OR \"Linux\") AND \"Backup\" AND \"Virtualization\"") — each AND multiplies
        the emptiness;
      - malformed Boolean (unbalanced quotes/parentheses, lowercase or/and, a
        location baked into the query text);
      - non-title jargon, internal grades, or req codes in the title slot
        ("SAP CO", "Java Developer II", "Band 4").

When the verdict is "risky" or "unsearchable", REPAIR it:
  • `suggestedSearchQuery` — a clean, short LinkedIn Boolean query: an OR-group of
    3–5 real target titles / one domain keyword + core titles, operators in
    UPPERCASE, double-quoted multi-word phrases, NO location text, NO long
    AND-blocks of skills. Leave EMPTY if the original query is already fine.
  • `suggestedTitles` — a repaired family of 4–8 real profile headlines in the
    SAME specialization, if the originals contain jargon/fragments/grades. Leave
    EMPTY if the originals are fine.
  Your repairs MUST stay in the exact same profession — fix the phrasing, never
  widen or shift the target. Changing the target is the recruiter's decision.

`issues` — one short plain sentence per problem, shown to the recruiter verbatim.
`reasoning` — one sentence naming the verdict and the single biggest reason,
referencing the actual query text.

Be strict but fair: do not flag a genuinely good short OR-group of real titles.
Most well-formed searches are "searchable" with no edits."""


def _build_agent() -> Agent:
    """Built lazily so importing this module never requires an API key."""
    return Agent(
        get_model("judge"),
        output_type=QueryJudgment,
        instructions=INSTRUCTIONS,
        retries=2,
    )


def _judge_prompt(strategy: SearchStrategy, job_description: str = "") -> str:
    f = strategy.filters
    payload = {
        "interpretedRole": strategy.interpretedRole or "",
        "focusTitle": strategy.focusTitle or "",
        "searchQuery": f.searchQuery or "",
        "currentJobTitles": list(f.currentJobTitles or []),
        "locations": list(f.locations or []),
    }
    out = [
        "Proposed search to judge:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    ]
    jd = (job_description or "")[:4000]
    if jd:
        out.append(f"\nJob description (context only — do not search it verbatim):\n{jd}")
    out.append("\nProduce the QueryJudgment.")
    return "\n".join(out)


async def judge_query(
    strategy: SearchStrategy, job_description: str = "",
) -> Optional[QueryJudgment]:
    """Vet the Strategist's titles + query. Returns None on any failure/no-op.

    Never raises — a Judge failure must not block the prefill, so every error
    path returns None and the Strategist's output stands unchanged.
    """
    f = strategy.filters
    if not (f.searchQuery.strip() or f.currentJobTitles):
        return None
    if not llm_available():
        logger.info("[Judge] no LLM key configured — skipping query vet")
        return None

    try:
        result = await _build_agent().run(
            _judge_prompt(strategy, job_description),
            usage_limits=UsageLimits(request_limit=2),
        )
        return result.output
    except Exception as exc:  # noqa: BLE001 — the vet is best-effort
        logger.error("[Judge] failed (%s) — leaving strategy unjudged", exc, exc_info=True)
        return None
