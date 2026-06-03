# Recruitr → Horizontal Expansion Strategy

> Should this become a multi-segment product (not just recruitment)? Research-backed
> analysis + the architecture and go-to-market sequencing to do it without dying of
> lack of focus. Companion to `PRODUCT_STRATEGY.md`.

---

## TL;DR

- **Architecturally: go horizontal.** The engine is already ~80% segment-agnostic. The only recruitment-specific pieces are the *signal*, the *persona map*, and the *offer/message* — abstract those into a **Playbook** config.
- **Commercially: do NOT launch 5–6 segments at once.** Near-unanimous research: vertical wedge first, then expand. One codebase ≠ one go-to-market.
- **The synthesis: _Horizontal architecture, vertical go-to-market._** Build pluggable; sell one segment at a time.
- **The reframe that matters: a generic "company posted a job" is a WEAK buying signal (+7%).** It's only strong when the role *is* the purchase (recruitment) or when stacked with funding / exec-hire / real headcount growth. The real generalization is **multi-signal**, not "same signal, more industries."

---

## 1. The category we're actually in

This is **signal-based selling / intent-based outbound / trigger-based GTM** — hot, crowded, well-funded.

| Player | Position | Signals | Price |
|---|---|---|---|
| **Clay** | Horizontal, $3.1B, 130+ data sources, "act on any signal" — *but needs a "GTM Engineer"* | hiring, funding, web, mentions | ~$149→$700+/mo |
| **Apollo** | Horizontal, SMB default (acquired Pocus for signals) | hiring, funding, leadership, tech | $49–99/user |
| **Common Room** | Horizontal signal aggregation/dashboard | job changes, hiring, funding, news | ~$12–30k/yr |
| **ZoomInfo** | Mid-market→enterprise DB + intent | funding, hiring, leadership "Scoops" | ~$15–60k/yr |
| **6sense** | Enterprise predictive ABM | funding, hiring, predictive | $60–200k+/yr |
| **Unify / Warmly / Trigify** | AI signal→outreach; visitor ID; LinkedIn signals (ToS risk) | funding, hiring, web | $700–1,500/mo |

**Two facts that define our opening:**
1. **The pricing canyon** — cheap-but-shallow ($50 tools, ~50% data accuracy) vs enterprise-but-overkill ($15–200k). Teams of 5–50 reps and SMB agencies are stuck in between, **underserved**.
2. **The market is shifting from "surfacing signals" → "acting on them"** (prioritize, explain, draft, send). Standalone signal dashboards are being absorbed/killed (Pocus→Apollo, Koala→shut down). **The pure horizontal signal dashboard is the least defensible position.**

→ Our defensibility is **vertical depth + done-for-you agentic execution + SMB affordability** — NOT breadth.

---

## 2. The signal-strength finding (changes the product)

Study of **1M B2B software purchases (2025)** — signal lift on future purchase:

| Signal | Lift |
|---|---|
| Bought an enterprise AI tool | **+46%** |
| Real headcount growth ≥20% | **+38%** |
| Recent software purchase (repeat buyer) | +38% |
| Hired a VP / exec | +28% |
| Raised funding (Pre-seed–Series E) | +25% |
| New office opening | +11% |
| **Job postings ↑ (generic)** | **+7% ← weak** |
| SOC compliance | 0% |

**Implications:**
- **Generic job-posting = the weakest validated trigger** (posting precedes the budget).
- **Category-relevant job posting is different & strong** — when the role *is* the purchase: "company hiring → needs a recruiter," "hiring 5 engineers → dev agency can pitch."
- **Recruitment is the perfect wedge** precisely because the job-posting signal is maximally predictive there by definition.
- **For all other segments: go multi-signal + signal-stacking** (2–3 signals on one account in a short window is the single biggest conversion lever in every source). Best-converting pair cited: **funding + new VP hire**.
- **Timing decays fast** — act in days, not weeks (funding window ≈ weeks 2–16 post-announcement).

---

## 3. Challenging the proposed segments

The unifying pattern of the good ones: **"a company is growing (hiring / funded / new exec) → someone sells them a B2B service."** Recruitment is the sharpest instance.

| Segment | Signal that actually works | Buyer / offer | Verdict |
|---|---|---|---|
| **Recruitment agencies** | Job posting (category-relevant ✅) + candidate loop | Agencies selling search services | ★ **Anchor wedge — start here** |
| **Dev / software / IT-staffing agencies** | Category-relevant hiring + funding | Agencies selling dev capacity | ★ Strong adjacent #2 |
| **Marketing agencies** | New CMO hire (+28%) + funding + product launch | Agencies selling marketing | ★ Strong adjacent |
| **Sales agencies / SDR-as-a-service** | Hiring SDRs/AEs + funding | Agencies selling outsourced sales | ★ Strong adjacent |
| **"Sell to funded startups" (founders)** | Funding round (+25%, public, legible) | Anyone selling B2B to startups | ★ Great signal-led playbook |
| **"Startups' product buyers / customers"** | — | — | ✗ **Descope** — finding end customers is demand-gen / PLG, a different motion. Engine is B2B account→person outbound, not consumer demand. |

---

## 4. Architecture — horizontal *internally* (the Playbook engine)

Make a **Playbook** the core abstraction. A segment = a playbook (config), not a code branch.

```
Playbook {
  signalSources:   [job_posting, funding, exec_hire, tech_change, headcount_growth]  // pluggable connectors
  stackingRule:    "fire when >= 2 signals co-occur within 30d"                       // the conversion lever
  icpFilter:       { industries, size, geo }                                          // who qualifies
  personaResolver: titles | agentic "who is the buyer for THIS offer at THIS account" // who to contact
  offer:           { what you sell, value props }                                     // feeds personalization
  outreach:        { personalization agent config, sequence }
}
```

**Signal-agnostic pipeline:**
```
SignalSource(s) → normalize to { Account, Trigger }
  → ICP qualify (agent)
  → resolve persona (agent)
  → personalize against offer (agent)
  → outreach
  → reply-handle (agent)
```

- Recruitment = `Playbook{ signal: job_posting, persona: hiring decision-maker, offer: search services }`.
- New segment = new playbook (mostly config + reuse funding/exec-hire connectors) → **marginal eng per segment → ~0.**
- This is Clay's actual path: wedge on a *function*, nail it, then expand — they did **not** launch 6 verticals on day one.

**Signal connectors to build (priority order):** job postings (have it) → funding (Crunchbase/news) → exec/leadership hire → category-relevant hiring → headcount growth → (later) tech-stack change, product launch, web-visit.

---

## 5. Recommended sequencing

1. **Win recruitment first** → ~20–50 paying agencies / $10–20k MRR. Best wedge: perfect signal + candidate loop + underserved + cheap niche GTM (vertical SaaS spends ~22% of revenue on S&M vs ~41% horizontal).
2. **Build the Playbook abstraction + multi-signal (funding + exec-hire) now** so it's config-ready — but keep *marketing* it as recruitment-only until #1 is proven.
3. **Expand one adjacent playbook at a time**, least resistance first: dev/IT agencies → marketing agencies → sales agencies → "sell-to-funded-startups." All are "growth-signal → B2B service," reachable via the same channels (LinkedIn, agency communities).
4. **Lean into the two open moats**: done-for-you *agentic execution* (personalize + reply-handle, not just alerts) and *SMB affordability* (no GTM-engineer required).

---

## 6. Hard truths

- **Don't sell 6 segments at once** — 6 segments = 6 GTM motions, 6 messages, 6 communities. A small team marketing all of them says nothing to anyone.
- **Generic job-posting signal is weak** — multi-signal/stacked everywhere except recruitment.
- **The horizontal signal-dashboard seat is taken & consolidating** — win the canyon (SMB agencies) with depth + execution, don't fight Clay/Apollo on breadth.
- **"Find customers for any product" is a different product** — descope.
- **Net:** generalizing is right *as architecture*, premature *as go-to-market*. Earn the right to go horizontal by owning recruitment first.

---

## Sources

- Signal correlation (1M purchases): https://bloomberry.com/blog/i-analyzed-1m-software-purchases-to-find-the-strongest-buyer-intent-signals/
- Practitioner signal ranking: https://overloop.com/blog/buying-signals-playbook
- Clay strategy/funding: https://techcrunch.com/2025/08/05/clay-confirms-it-closed-100m-round-at-3-1b-valuation/ · https://sacra.com/c/clay/
- Vertical wedge strategy: https://a16z.com/vertical-operating-systems-one-system-of-record-to-rule-them-all/ · https://www.saastr.com/big-addressable-market-go-vertical-saas-good-idea-avoid-addressable-market-appears-small/ · https://tomtunguz.com/vertical-saas-tradeoff/
- Pricing canyon / market gap: https://salesmotion.io/blog/zoominfo-too-expensive-alternatives
- Consolidation: https://salesmotion.io/blog/apollo-acquires-pocus · https://www.marketbetter.ai/blog/best-koala-alternatives-2026/
- Vertical-vs-horizontal GTM: https://www.saasmag.com/vertical-saas-niche-beats-horizontal-2026/ · https://www.olivinemarketing.com/articles/how-to-define-target-vertical-for-horizontal-saas-product
