# Signal Detection Engine — Pure Agentic Architecture

> A production-grade design for a fully agentic signal detection & action engine.
> Companion to `PRODUCT_STRATEGY.md` and `HORIZONTAL_EXPANSION_STRATEGY.md`.
> Researched against the current 2026 state of agentic AI (Anthropic patterns,
> memory architectures, LangGraph/CrewAI, MCP, evaluator-optimizer reliability).

---

## 0. The Discipline ("Pure Agentic" = what it actually means)

There's a trap in the phrase "pure agentic AI." Most teams interpret it as
*"everything is an LLM agent"* — which is how you build a slow, expensive,
unreliable system. The correct discipline is:

> **Agents own decisions, language, and judgment. Deterministic code owns
> plumbing, persistence, idempotency, and rate-limiting.** Together they form
> a "pure agentic" *system* — one where the meaningful work (deciding,
> reasoning, composing) is done by agents end-to-end, but agents never replace
> what code does better.

Anthropic's own guidance (which built the multi-agent Research system that
out-performed single-agent Claude Opus 4 by ~90% on internal evals) is explicit:
*"Use the simplest pattern that works. Multi-agent is for problems where
**parallel exploration** of an **open-ended search space** beats sequential
single-agent reasoning."* Signal detection is exactly that problem.

[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) ·
[Anthropic — Multi-Agent Research System](https://www.anthropic.com/engineering/multi-agent-research-system)

---

## 1. State of Agentic AI — What's Actually Production-Viable (2026)

| Capability | Status | Implication for us |
|---|---|---|
| **Tool use** | Standardized via **MCP** (97M+ SDK downloads, OpenAI + Anthropic + Google adoption). Gartner: 75% of API gateways will be MCP-compatible by EOY 2026. | Every external API (Apollo, Crunchbase, news, scrapers, DB) becomes an MCP server. Drop-in for any model. |
| **Orchestration** | **LangGraph** is the production standard (stateful DAGs, checkpointing, time-travel, audit). CrewAI for prototypes. OpenAI Swarm archived. | LangGraph is the spine. |
| **Multi-agent (orchestrator-workers)** | Proven pattern; Anthropic's Research system parallelizes 3–5 subagents and beats single-agent by ~90% on browsing-heavy tasks. | This is *exactly* how to fan out across signal sources. |
| **Memory** | 4-tier model (working / procedural / semantic / episodic) converged on; production options: **Mem0**, **Letta**, **Zep**, **Graphiti**. Hybrid (vector + graph + episodic store) is the 2026 default. | Long-running engine needs all four. Not optional. |
| **Reflection / Evaluator-Optimizer** | Standard pattern. Generator agent → independent critic agent → revise loop. Critic should use a different model family or external signal to avoid bias. | The quality gate. Catches hallucinated signals before they ship to outreach. |
| **Planning** | ReAct (think→act→observe) for short tasks; **Plan-and-Execute** for long-horizon. | Plan-and-Execute fits "given an ICP, detect this week's signals." |
| **Reliability primitives** | Bounded execution (max iterations/tool calls), guardrail layering (input + output schemas), trajectory logging, span-level evaluators, human-in-the-loop gates. | All required. Drift, looping, and cost blowouts are the #1 production failures. |
| **Framework-induced performance variance** | Princeton HAL benchmark: **same model + different scaffold = ±30 percentage points**. | The scaffold matters as much as the model. Don't pick a framework casually. |

[LangGraph vs CrewAI 2026](https://gurusup.com/blog/best-multi-agent-frameworks-2026) ·
[Memory frameworks ranked](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/) ·
[MCP enterprise guide](https://dev.to/x4nent/complete-guide-to-mcp-model-context-protocol-in-2026-architecture-implementation-and-4a11) ·
[Reflection pattern](https://medium.com/@swapnilshekade/reflective-and-self-improving-agents-building-ai-systems-that-critique-iterate-and-learn-from-fd3a57f62085)

---

## 2. The Problem Restated (so the architecture maps to it)

A **Signal** is a time-bounded fact about a target account that makes it more
likely to buy a B2B service *right now*. From the 1M-purchase study referenced
in our expansion doc:

| Signal | Buyer-intent lift |
|---|---|
| Enterprise AI purchase | +46% |
| Headcount growth ≥20% | +38% |
| New VP / exec hire | +28% |
| Funding round | +25% |
| New office | +11% |
| Generic job posting | +7% (weak alone) |
| **Stacked: 2+ signals in 30 days** | **multiplicative — the conversion lever** |

The engine's job is to, *per ICP playbook*:
1. **Discover** candidate signals across heterogeneous, messy sources
2. **Validate** each one (deduplicate, verify, ground in evidence)
3. **Stack** related signals on the same account inside a decaying time window
4. **Score** the resulting bundle for buyer-intent strength against this offer
5. **Explain** ("why now" hook) — the durable artifact outreach uses
6. **Route** to the right playbook with the right persona + first-touch draft
7. **Learn** from downstream outcomes (replies, meetings, deals) to re-weight

This is open-ended, source-heterogeneous, requires judgment at every step, and
must run continuously and cheaply. That's the agentic sweet spot.

---

## 3. System Architecture (the picture)

```
                              ┌──────────────────────────────────────────────┐
                              │            Playbook Registry (DB)            │
                              │ recruitment · dev-agency · marketing · ...   │
                              └──────────────────────────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          ORCHESTRATOR AGENT  (LangGraph)                     │
│  • Reads active playbooks                                                    │
│  • Plans: "for playbook X, what signals to hunt, where, in what window?"     │
│  • Fans out subagents in parallel                                            │
│  • Synthesizes results into account-bundles                                  │
│  • Routes to evaluator → action agents                                       │
│  • Checkpoints state at every node (audit + replay)                          │
└──────────────────────────────────────────────────────────────────────────────┘
       │              │                  │                 │                │
       ▼              ▼                  ▼                 ▼                ▼
┌────────────┐  ┌────────────┐    ┌────────────┐    ┌────────────┐   ┌────────────┐
│ JobPost    │  │ Funding    │    │ ExecHire   │    │ HeadcountΔ │   │ News/Tech  │
│ Hunter     │  │ Hunter     │    │ Hunter     │    │ Hunter     │   │ Hunter     │
│ (Agent)    │  │ (Agent)    │    │ (Agent)    │    │ (Agent)    │   │ (Agent)    │
└────────────┘  └────────────┘    └────────────┘    └────────────┘   └────────────┘
       │              │                  │                 │                │
       └──────┬───────┴──────────┬───────┴────────┬────────┴────────────────┘
              ▼                  ▼                ▼
       MCP TOOL LAYER (deterministic, single source of truth for I/O)
       Apollo · Crunchbase · NewsAPI · LinkedIn · Firecrawl · DB · Search
              ▼                  ▼                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         SIGNAL VALIDATOR  (Agent)                            │
│  • Dedupe against episodic memory ("seen this signal before?")               │
│  • Ground in evidence (each claim must cite source URL + snippet)            │
│  • Reject low-confidence / stale / ungrounded signals                        │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                       SIGNAL STACKER  (deterministic + agent)                │
│  • Group validated signals by account_id                                     │
│  • Apply decay function (signal weight × age curve)                          │
│  • Bundle: { account, signals[], window, raw_score }                         │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                  SCORING + WHY-NOW AGENT  (Generator)                        │
│  • Reads bundle + playbook offer + semantic memory ("what closed before")    │
│  • Produces:  score (0-100) · confidence · "why now" hook · suggested CTA    │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    EVALUATOR  (Critic, different model family)               │
│  • Independent grading against rubric (evidence? freshness? offer-fit?)      │
│  • If fail → revise loop (max 2) OR drop with reason                         │
│  • If pass → emit Signal Bundle to action layer                              │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    ACTION AGENTS  (per playbook)                             │
│  Persona Resolver → Personalization Agent → Outreach Sender → Reply Agent    │
│  (Human-in-the-loop gates configurable per agency/tier)                      │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                          MEMORY (4-tier, hybrid)                             │
│  Working (LangGraph state) · Procedural (system prompts + tool schemas)      │
│  Semantic (vector: "what worked", "ICP refinements") — pgvector / Qdrant     │
│  Episodic (timestamped: every signal seen, every action, every outcome)      │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│         FEEDBACK LOOP — Outcomes (reply / meeting / deal) flow back to       │
│         semantic memory and re-weight scoring + playbook rules over time.    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. The Agents — One Section Each

### 4.1 Orchestrator Agent (the brain)

- **Pattern:** Orchestrator-Workers + Plan-and-Execute. (Anthropic's Research-system pattern.)
- **Lives in:** LangGraph — directed graph with conditional edges, checkpointed at every node.
- **Loop per tick** (e.g. hourly cron or webhook):
  1. Load all active playbooks from the registry.
  2. For each playbook: produce a **search plan** — which signal sources, which ICP filter, which time window, which max-cost budget.
  3. Spawn **Hunter subagents** in parallel (one per signal source, isolated context to avoid context pollution — Anthropic's key finding).
  4. Wait for completion (or partial completion past a timeout).
  5. Hand off raw signals to the Validator → Stacker → Scorer → Evaluator → Action chain.
- **Why this is critical to be agentic, not coded:** the plan changes per playbook, per market condition, per recent outcomes. "Stop hunting funding signals for the recruitment playbook on Sunday because no deals close on Mondays" — that's a judgement, not a rule.

### 4.2 Hunter Subagents (one per signal type)

Each Hunter is a **specialist** with: a clear objective, a strict output schema, a small tool pool, a token/cost budget, and a hard stop on iterations.

| Hunter | Sources (via MCP) | Output |
|---|---|---|
| **JobPost Hunter** | JobSpy MCP · LinkedIn Jobs MCP · Indeed MCP | role + company + posted_at + URL |
| **Funding Hunter** | Crunchbase MCP · NewsAPI MCP · Firecrawl MCP (TechCrunch/EU-Startups) | round + amount + investors + announced_at |
| **ExecHire Hunter** | LinkedIn (managed) · NewsAPI · press-release search | name + new_title + company + start_date |
| **Headcount Hunter** | Apollo MCP (headcount-growth fields) · LinkedIn employee count delta | growth % + window |
| **News/Tech Hunter** | NewsAPI · BuiltWith MCP · Firecrawl deep-search | event type + headline + URL |

Hunters use **ReAct** (think → call tool → observe → think → …), bounded to ~10 tool calls + ~30k tokens, with a structured-output contract enforced by the orchestrator. A hunter that exceeds budget or returns malformed output gets a single retry with a tightened prompt then is dropped for the tick.

### 4.3 Signal Validator

The single most under-built component in most "signal" tools, and the reason they get a bad rep for noise.

- **Inputs:** raw signal candidates from all Hunters.
- **Job:**
  1. **Dedupe** against episodic memory ("did I see this same exec-hire on Monday?")
  2. **Ground** — every signal must carry source URL + cited snippet. If it can't be re-resolved, drop it.
  3. **Freshness check** — apply per-signal freshness ceiling (e.g. funding: 90 days; job post: 14 days).
  4. **Sanity check** — agent reads the snippet and answers a strict yes/no: "does this snippet *actually* support the claim?" This catches LLM hallucinations and bad scrapes.
- **Pattern:** small specialist agent + JSON-Schema-validated output. Tools: 1 (re-fetch URL).

### 4.4 Signal Stacker

Mostly deterministic — this is the right place for code:

```
for each account_id appearing in validated signals:
    bundle = collect all signals for that account in last 60d
    apply decay: weight_i × exp(-age_days / half_life_signal)
    raw_score = sum of decayed weights
    record bundle with its component signals + score
```

The agentic part: when the bundle is unusual (e.g. funding + headcount drop), a small **stacker-reasoner** agent annotates the bundle with a *narrative*: "company raised funds but is shrinking — likely restructuring, not growth." Saves the scorer from misreading.

### 4.5 Scoring + Why-Now Agent (Generator)

The "judgment" core. Reads:
- The bundle (signals, evidence, narrative)
- The playbook (offer, ICP, value props)
- **Semantic memory**: "for this offer, which signal bundles closed deals in the past 90 days?"

Emits:
```
{
  account_id,
  buyer_intent_score: 0-100,
  confidence: 0-1,
  why_now_hook: "1KOMMA5° just raised €300M and hired a VP Engineering
                 last week — they're scaling the technical org from
                 ~150 to >300 in 12 months.",
  recommended_persona_role: "VP Engineering or Head of Talent",
  recommended_cta: "15-min intro: 3 senior backend hires we've closed
                    in similar German cleantech scale-ups",
  trace: { signals_used, memory_refs, model_calls, cost }
}
```

The `why_now_hook` is the **most valuable durable artifact in the whole system** — it's what makes downstream outreach not feel like spam.

### 4.6 Evaluator (Critic)

Independent agent — **must use a different model family from the generator** (e.g. generator = Claude, critic = GPT, or vice-versa) to break shared-prior bias. Grading rubric:

- Is every claim in `why_now_hook` grounded in a cited signal? (binary fail)
- Is the persona recommendation plausible for the offer? (1–5)
- Is the CTA specific to *this* company? (1–5)
- Is the score within ±20 of a deterministic baseline score? (sanity bound)

Below threshold → one revise loop with the critique appended → re-evaluate.
Below threshold again → drop with reason logged. **Bounded execution at 2 revisions max.** This is the single biggest reliability lever.

### 4.7 Action Agents (per playbook)

The chain that makes signals into revenue:

1. **Persona Resolver Agent** — given the bundle + recommended_persona_role + the account's people (from Apollo), pick the actual person. Reasoned, not just title-matched.
2. **Personalization Agent** — drafts the first-touch email/LinkedIn message referencing the `why_now_hook`, the persona's recent activity (one quick MCP call), the offer's specific value prop. ★ **The revenue-generating agent.**
3. **Outreach Sender** — deterministic. Email API, schedule, throttle, suppression check.
4. **Reply Agent** — classifies replies (interested/not/refer/OOO), drafts responses, proposes meeting times, escalates to human when uncertain.

Each agent runs inside a **human-in-the-loop gate** that's configurable per agency (off, sample 1-in-N, every message, every reply).

---

## 5. Memory Architecture (the long-running brain)

**Four tiers — all production-required, not optional for a continuous engine:**

| Tier | What lives there | Backed by |
|---|---|---|
| **Working** | The current orchestrator tick's state, tool outputs, in-flight reasoning | LangGraph state (Redis/Postgres checkpoint) |
| **Procedural** | System prompts, tool schemas, playbook templates, scoring rubric | Versioned files in repo + DB; updated via deploy or admin UI |
| **Semantic** | Embedded "facts that matter": "VP Eng hires at €100M+ cleantech rounds → 73% reply rate to <CTA pattern>"; ICP refinements learned from outcomes | pgvector or Qdrant; written by feedback loop |
| **Episodic** | Time-stamped log of every signal observed, every action taken, every outcome (reply/meeting/deal) | Postgres (relational) for queryable; mirror to vector for similarity recall |

**Memory operations** the agents have as MCP tools:
- `remember_outcome(signal_bundle_id, outcome)` — writes both semantic + episodic
- `recall_similar_wins(current_bundle, k=5)` — what worked before for this pattern
- `recall_seen(signal_fingerprint)` — dedup
- `lessons_for(playbook_id)` — current procedural overrides from feedback

**Why this matters:** without this, the engine is amnesic — it'll re-surface the same noisy account every week, can't learn that "Series C cleantech in Germany" overconverts vs "Series A SaaS in US," and can't get sharper over time. Memory is the difference between "another scraper" and "an engine that compounds."

[Letta / MemGPT 3-tier architecture](https://hermesos.cloud/blog/ai-agent-memory-systems) ·
[Memory frameworks ranked 2026](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/)

---

## 6. Reliability Spine (non-negotiable)

These are what separate a demo from a product:

1. **Bounded execution** — every agent has hard caps: max iterations, max tool calls, max tokens, max wall-clock, max $cost. Configured per agent, enforced by the orchestrator.
2. **Schema-validated I/O at every edge** — every agent emits JSON validated by Pydantic / JSON Schema. Failure → one tighten-and-retry → drop with logged reason.
3. **Trajectory logging** — every node in the graph writes a trace: inputs, outputs, model call, tokens, cost, latency, decisions. The audit trail *is* the debugging tool.
4. **Span-level evaluators** — offline evals replay traces against rubrics nightly. Regression on win-rate triggers an alert.
5. **Independent critic** — Evaluator uses a different model family from the Generator. Catches shared-prior hallucinations.
6. **Human-in-the-loop gates** — configurable per agent per agency. Default: every first outreach approved by human in first 30 days post-onboarding, then sample 1-in-10.
7. **Cost monitor** — per-playbook, per-tick budget. Soft alert at 70%, hard stop at 100%. Without this, one bad recursion eats $200 overnight.
8. **Idempotency keys** — every external write (send email, write DB) carries an idempotency key derived from `(signal_bundle_id, action_type)` to survive retries.
9. **Circuit breakers** on every MCP tool — Apollo 429s, LinkedIn blocks, Firecrawl downtime should degrade gracefully, not cascade-fail the whole tick.
10. **Sandbox mode** — every new playbook runs read-only against episodic memory for 2 weeks (suggests outreach, doesn't send) so the team can grade before live fire.

---

## 7. Technology Stack (concrete picks)

| Layer | Pick | Why |
|---|---|---|
| Orchestration | **LangGraph** | Production standard 2026; stateful, checkpointed, time-travel debugging, audit-friendly. CrewAI is faster for prototyping but doesn't scale to graphs of this complexity. |
| Models | **Claude Sonnet (Generator) + GPT-4-class (Critic)** + Haiku/4o-mini for hunters | Different families break shared-prior bias on the critic; small models for cheap parallel hunting. |
| Tool integration | **MCP** — every external API behind an MCP server | One protocol → swap models freely, swap providers freely. Aligns with the 2026 ecosystem direction. |
| Memory | **pgvector** (semantic) + **Postgres** (episodic) + **Mem0** library (write-side abstraction) | All open-source, all your DB, no vendor lock. Mem0 if you want managed long-term memory; raw pgvector if cost-sensitive. |
| State / queue | **Redis** for LangGraph checkpoints; **Celery** or **Temporal** for long-running ticks | Temporal if reliability is critical — gives you durable workflows, retries, time-travel for free. |
| Observability | **Langfuse** or **Arize Phoenix** | Span-level traces, eval harness, cost dashboards — the LangSmith competitors that don't lock you in. |
| MCP servers we'll build/use | Apollo, Crunchbase, NewsAPI, Firecrawl, LinkedIn-managed (Unipile), JobSpy wrapper, internal DB | One server per data source. Reusable across playbooks. |

---

## 8. Cost Model (rough, per tick per playbook per agency)

For an SMB agency on the $249/mo plan with ~50 target accounts/week:

| Component | Calls/week | Cost |
|---|---|---|
| Hunters (5 × Haiku-class, ~5k tok each) | 250 | ~$0.50 |
| Validator (Sonnet, ~3k tok per signal) | 80 | ~$1.50 |
| Scorer/Generator (Sonnet) | 50 | ~$2.00 |
| Critic (GPT) | 50 | ~$1.50 |
| Persona + Personalization | 50 | ~$3.00 |
| Reply handling (varies) | 30 | ~$1.50 |
| MCP data costs (Apollo enrichments etc.) | mixed | ~$8–15 |
| **Total compute** | | **~$18–25/week** |
| **Monthly** | | **~$80–100/agency** |

Against $249/mo plan price → **60% gross margin** even at high agent intensity. Margin improves to 75%+ as memory amortizes (the engine gets sharper / cheaper per useful signal over time).

---

## 9. Build Plan (phased — earn complexity)

The trap to avoid: building all of this at once. Each phase ships value standalone.

**Phase 0 — Foundation (week 1–2)**
- LangGraph spine + Postgres state + Redis checkpoints
- 1 MCP server: Apollo (org + people + match)
- 1 Hunter (Funding) + 1 Validator + Stacker
- No Generator yet — output to a dashboard "raw signals" view
- Goal: prove the pipe end-to-end on one signal type.

**Phase 1 — One full playbook (week 3–6)**
- Add 2 more Hunters (JobPost, ExecHire)
- Generator (Why-Now) + Critic (Evaluator-Optimizer loop)
- Episodic + Semantic memory (pgvector)
- Recruitment playbook only, sandbox mode (suggests outreach, doesn't send)
- Goal: prove the agentic loop produces useful "why now" hooks a human would approve.

**Phase 2 — Action layer (week 7–10)**
- Persona Resolver + Personalization Agent
- Outreach Sender (email infra, deliverability)
- Human-in-the-loop gates + approval UI
- Goal: live send, with humans grading every message for the first 2 weeks.

**Phase 3 — Reply + feedback loop (week 11–14)**
- Reply Agent
- Outcome capture (reply/meeting/deal) → semantic memory writes
- Nightly regression evals
- Goal: the engine starts learning. Win-rate trends visible in dashboard.

**Phase 4 — Second playbook (week 15–18)**
- Add dev-agency playbook (mostly config + reuse Hunters)
- Validates the horizontal architecture
- Goal: 2 playbooks live, marginal-cost-to-add-third demonstrated.

**Phase 5 — Operational maturity (ongoing)**
- Trajectory eval CI/CD
- Cost-per-replied-meeting dashboards (the real product metric)
- Per-agency tuning via procedural overrides

---

## 10. Why This Is Defensible (the moat)

A bare scraper or a Clay competitor can be cloned in a quarter. This architecture compounds:

1. **The episodic memory becomes proprietary training data** — every reply/meeting/deal recorded grows a corpus no competitor has.
2. **The semantic memory becomes a per-playbook lessons-learned database** — the engine for "German cleantech recruitment" gets visibly sharper than a generic Apollo+Clay stack month-over-month.
3. **The agent rubric & critic** — tuned against actual booked-meeting outcomes — is unscoopable by API access alone.
4. **Multi-source signal stacking + evidence-grounded "why now"** is the artifact buyers actually care about; pure signal dashboards (Common Room, Pocus pre-acquisition) couldn't ship this and that's why they got absorbed.

The defensibility is not the agents themselves (frameworks are commoditizing fast). It's the **closed loop of signals → action → outcomes → memory → better signals** running in production with real feedback data flowing in for months.

---

## 11. Hard Trade-offs You're Signing Up For

Be honest about these — they're real and they're worth it, but go in eyes open:

- **Latency.** A pure agentic pipeline has Generator → Critic → maybe-revise loops; per-bundle latency is 5–30s. Fine for hourly ticks, **wrong for click-to-result UX**. Synchronous user actions stay deterministic.
- **Non-determinism by design.** Same input ≠ same output. Mitigated by: trajectory logging, eval suites, sandbox mode, human-in-the-loop in the first 30 days. Not eliminated.
- **Cost spikes are real.** A bad recursion can eat $50 in 10 minutes. The bounded-execution + cost-monitor primitives are not optional.
- **Model-version dependence.** Agent quality moves when Claude/GPT update. You need a regression eval suite or you'll discover prod regressions from customers.
- **Framework lock-in is real even in 2026.** LangGraph's mental model bleeds into your code. Pick once, stick with it — Princeton showed ±30pp performance swings just from scaffold choice.
- **MCP is young** — server quality varies wildly. Plan to maintain your own MCP servers for your top 3 data sources; don't rely on community ones for production paths.

---

## 12. Sources

- [Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)
- [Anthropic — Multi-Agent Research System (architecture deep-dive)](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Orchestrator-Workers cookbook (Anthropic)](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/orchestrator_workers.ipynb)
- [Agentic AI Design Patterns 2026](https://www.innovatrixinfotech.com/blog/agentic-ai-design-patterns-react-reflection-tool-use)
- [The State of AI Agents in 2026 — Kingy](https://kingy.ai/ai/the-state-of-ai-agents-in-2026-a-practitioners-guide/)
- [LangGraph vs CrewAI vs Swarms 2026](https://gurusup.com/blog/best-multi-agent-frameworks-2026)
- [AI Agent Framework Showdown 2026](https://qubittool.com/blog/ai-agent-framework-comparison-2026)
- [Best AI Agent Memory Frameworks 2026](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/)
- [Memory architectures — Letta/MemGPT, Mem0, Zep](https://hermesos.cloud/blog/ai-agent-memory-systems)
- [Vector vs Graph vs Episodic memory](https://www.digitalapplied.com/blog/agent-memory-architectures-vector-graph-episodic)
- [Reflective and Self-Improving Agents](https://medium.com/@swapnilshekade/reflective-and-self-improving-agents-building-ai-systems-that-critique-iterate-and-learn-from-fd3a57f62085)
- [ReAct vs Plan-and-Execute vs Reflection (2026)](https://dev.to/gabrielanhaia/react-plan-and-execute-or-reflection-the-three-agent-patterns-every-engineer-needs-in-2026-355p)
- [MCP Complete Guide 2026](https://dev.to/x4nent/complete-guide-to-mcp-model-context-protocol-in-2026-architecture-implementation-and-4a11)
- [MCP Production-Grade Agents](https://dev.to/monuminu/model-context-protocol-mcp-the-complete-developer-guide-to-building-production-grade-ai-agents-ah3)
