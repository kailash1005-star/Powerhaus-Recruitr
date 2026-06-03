# Recruitr — Product Strategy & Business Roadmap

> A founder-grade read on what we've built, whether it sells, where agentic AI
> earns its keep, pricing, go-to-market, and the build roadmap. Written to be
> picked up later and executed against.

---

## 1. What we've actually built (named honestly)

This is a **recruitment-agency revenue engine**, not a "scraper." The loop:

> **Hiring signal** (company posted a role) → **qualify the company** (industry / size / fit)
> → **find the decision-maker to pitch** (BD lead) → **enrich contact**
> → *(win the client)* → **source candidates** for that role.

That closed loop — *BD lead-gen + candidate sourcing in one system, triggered by
hiring intent* — is more than the sum of its parts. Most tools do **one** half.

**Current architecture (as built):**
- **Phase 1** — Scrape job postings (JobSpy → LinkedIn) by title + location + ICP config; executive/leadership title rejection filter; dedupe; store.
- **Phase 2** — For each accepted job's company, fetch company metadata (domain, industry, employee count, HQ) from LinkedIn; semantic industry match via OpenAI against the user's target industries; accept/reject (targeted if industry matches + size < 10k + not a staffing agency). Rejected companies' jobs flipped to rejected with reason.
- **Phase 3** — Apollo people search (FREE endpoint) to find prospects (decision-makers: CEO/COO/HR etc.) at accepted companies = B2B leads to pitch recruitment services to. Manual Apollo `/people/match` enrichment (email/phone) for outreach.
- **Phase 4** — Candidate pipelines: for a won role, source actual candidates via Apollo people search (semantic title + location + current-industry, with industry fallback), accept/reject, manual enrich. Background per-job search with an atomic state machine (queued → running → completed/failed), parallel-safe, re-run appends + skips previously rejected.
- **Outreach** — stubbed ("Soon").
- **Surfaces** — Runs nav, Candidates nav, Dashboards, ICP config, Settings, Integrations.

**Strengths today:** hiring-intent trigger, clean phase orchestration, accept/reject
auditability, candidate pipelines, polished UI.

**The honest gap:** it currently produces **lists**. Lists are a commodity (Apollo
sells them for $49/mo). The money in this category is made *after* the list —
outreach, personalization, replies, booked meetings. Outreach is stubbed.
**That stub is the difference between a $0 product and a $100k ARR product.**

---

## 2. Is it saleable? Yes — but be clear which half

| Half | Market | Reality |
|---|---|---|
| **BD lead-gen for recruiters** (companies hiring + who to pitch) | Smaller, underserved | **This is the wedge.** Few tools help recruitment *agencies* win new *clients*. |
| **Candidate sourcing** | Huge but brutal | Competing with LinkedIn Recruiter ($835/seat), hireEZ, SeekOut, well-funded. Don't lead here. |

**Lead with the BD engine.** One-liner:
> *"We tell recruitment agencies which companies are hiring right now, who to call,
> and write the first message — so they win more retained search clients."*

The candidate-sourcing loop is the **retention/expansion** feature, not the wedge.

---

## 3. Who buys it, and where to sell

**ICP (tightest first):** boutique & specialist recruitment agencies, **2–30 recruiters**,
especially exec search / niche verticals (tech, healthcare, engineering, finance).
They live on BD, can't afford an SDR team, won't build this themselves.

**Where to find them:**
- **Eat your own dog food** — use the product to find agencies and pitch them (strongest possible demo).
- Recruitment communities: r/recruiting, The Recruitment Network, agency-owner LinkedIn groups, recruiter Slack/Discord.
- **Partnerships / marketplaces**: Bullhorn / Vincere / JobAdder / Crelate app marketplaces (where agencies already are).
- AppSumo / lifetime-deal launch for early logos + cash.
- Geo: dense agency markets — **UK, DACH, US, India, MENA, ANZ** (already testing Germany/EU).

---

## 4. Pricing (concrete)

Benchmarks: Apollo $49–99/user · LinkedIn Recruiter Lite ~$170 · full Recruiter ~$835/seat · hireEZ/SeekOut enterprise.

**Recommended: hybrid seat + usage.**

| Tier | Price | Includes | Target |
|---|---|---|---|
| **Starter** | **$99/mo/seat** | N runs, X company qualifications, Y enrichments, basic outreach | Solo / 1–2 recruiters |
| **Agency** | **$249–299/mo/seat** (or $499 for 3 seats) | Higher limits, agentic personalization, reply handling, candidate pipelines | 3–15 recruiters |
| **Scale** | **Custom ($800–1,500/mo)** | Team analytics, CRM sync, priority data, dedicated session infra | 15+ |

Plus **usage credits** for enrichment beyond plan (pass-through Apollo cost + margin). Annual −20%.

**Unit economics (rough, per active seat/mo):**
Apollo (org enrich + matches) ~$15–40 · OpenAI (qualification + personalization) ~$3–10 ·
proxy/managed LinkedIn ~$5–15 · infra/email ~$5 → **cost-to-serve ≈ $30–70**,
price $99–299 → **70–80% gross margin.**

---

## 5. Where agentic AI *actually* belongs

Discipline: **keep the plumbing deterministic; put agents only where there's
judgment, research, or language.** Agents in the scrape/store layer = slower,
flakier, costlier for zero gain.

| Layer | Today | Agentic upgrade | Value |
|---|---|---|---|
| Scrape / dedupe / store | Deterministic ✅ | **Keep deterministic** | — |
| **Company qualification** | One-shot GPT industry match | **Qualification agent**: reads recent news, funding, headcount growth, tech changes → "good agency client right now?" + reasoning trace + "why now" hook | ★★★ |
| **Decision-maker selection** | Persona-title heuristics | **DM-reasoning agent**: who's the real buyer for *this* role at *this* company, with rationale | ★★ |
| **Outreach** (stub) | — | **Personalization agent**: per-prospect first touch referencing exact role + why-now signal; multi-step sequence; A/B angles | ★★★★ **revenue lever** |
| **Reply handling** | — | **Inbox agent**: classify replies, draft responses, propose meeting times | ★★★ |
| **Candidate screening** | Heuristic match score | **Fit agent**: reason candidate↔role, draft "why this candidate" blurb for the client pitch | ★★★ |
| **Predictive sourcing** | "posted a job" | **Signal agent**: fuse job posts + funding + growth + exec departures → "companies *about to* need a recruiter" | ★★ moat |

**Build first: outreach personalization agent.** Clearly agentic, moves the metric
agencies pay for (booked meetings), and is what makes us *not* "just another list."

**Architecture:** an **agent-orchestration layer** — a supervisor that per accepted job runs
`qualify → pick DM → personalize → (send) → handle reply`, each step a tool-using agent,
with human-approval gates where the recruiter wants control. Agents live at the
*decision and language* edges, wrapped around the deterministic data spine.

---

## 6. Roadmap (Now / Next / Later)

**NOW (next 30 days) — make it sellable, not just functional**
1. **Reliability**: move company data to **Apollo Org Enrichment** (kill the LinkedIn-cookie single point of failure). A demo that breaks mid-call kills deals.
2. **Outreach v1 (real)**: send + the **personalization agent**. Without this we sell a list.
3. **ROI analytics**: dashboard showing *meetings booked / replies / deals influenced* — agencies buy outcomes, not "prospects found."
4. **Legal hygiene**: GDPR-compliant cold email (legitimate-interest basis, unsubscribe, suppression list), domain warming, SPF/DKIM/DMARC.

**NEXT (30–90 days) — differentiate**
5. **Qualification agent** + "why now" hook feeding outreach.
6. **Reply-handling agent** + light CRM (prospect → conversation → client → role → candidate as one funnel).
7. **Candidate fit agent** + "why this candidate" generator.

**LATER (90 days+) — moat & expansion**
8. **Predictive signal agent** (intent before the job post).
9. **Integrations**: Bullhorn / Vincere / JobAdder sync (distribution + stickiness).
10. Team features, multi-session infra, white-label.

---

## 7. Replace / Change / Introduce — at a glance

- **Replace:** LinkedIn-cookie company lookup → Apollo Org Enrichment (reliability). Single-shot GPT classification → qualification agent (when ready).
- **Change:** Outreach stub → real product surface (the monetization moment). Analytics from "counts" → "outcomes."
- **Introduce:** Agent-orchestration layer (personalize → reply → screen), light CRM/funnel, ROI dashboard, GDPR/deliverability infra, CRM integrations.
- **Keep:** the deterministic scrape→filter→match spine, accept/reject auditability, the closed BD→candidate loop (the actual differentiation).

---

## 8. Hard truths (so we build the right thing)

1. **Without outreach, we're reselling Apollo.** An agency could approximate our list with Apollo + Clay. Defensibility = *hiring-intent trigger + recruitment-specific closed loop + agentic personalization/screening that saves hours*. Build toward that, not more data.
2. **EU cold email is legally loaded (GDPR).** Need consent/legitimate-interest handling, suppression, clean sending infra. Non-optional for a sellable product.
3. **Deliverability is a product, not a checkbox** — warmed domains, DMARC, volume pacing.
4. **Don't lead with candidate sourcing** — incumbents will crush it. Lead BD, expand into candidates.

---

## 9. Reliability note (data layer)

A hand-managed LinkedIn `li_at` cookie **cannot be made unbreakable** — it's a borrowed
consumer session LinkedIn can revoke anytime (logout, password change, security
checkpoint, datacenter IP, activity spike, server-side rotation). Three tiers:

- **Tier 1 — Harden current cookie**: session health probe, alert on death (no silent GPT fallback), DB-stored hot-swappable cookie, pinned residential/mobile proxy, pacing + jitter. Recoverable in minutes, still fragile.
- **Tier 2 — Managed account-based API** (e.g. Unipile): hosted auth, checkpoint handling, auto-reconnect via stored TOTP, account-status webhooks. Self-healing; monthly fee per connected account.
- **Tier 3 — Remove LinkedIn from the critical path** (recommended): everything Phase 2 needs (domain, industry, employee count, HQ) is in Apollo's Organization Enrichment, and the domain it returns is the exact one Apollo's people index uses → matches even better than LinkedIn's. Costs Apollo credits once per unique company; no session, no bans.

**Recommendation: Tier 3 for company data + Tier 1 hardening for any remaining
LinkedIn-only needs.**

---

## 10. Recommended first moves

1. **Apollo Org Enrichment reliability migration** (1–2 days) — solid foundation.
2. **Outreach v1 + personalization agent** — the monetization layer.

Reliability first, then layer agents on top.
