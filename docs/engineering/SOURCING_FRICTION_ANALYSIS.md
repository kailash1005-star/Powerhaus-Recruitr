# Sourcing Pipeline — Friction Analysis (post combined-discovery push)

**Date:** 2026-07-22 · **Status:** analysis only, no code changed · **Awaiting approval to implement**
**Scope:** the developer's `56b1d9e "Integrated Prospect Identifying"` (pushed 2026-07-21) vs today's live SAP-CO/PS test run.

---

## TL;DR — three confirmed root causes

| # | Symptom you saw | Real cause | Verdict |
|---|---|---|---|
| 1 | "Apify keeps hitting a rate limit, and changing the API key doesn't fix it" | It is **not** a rate limit — it's the **Apify free-plan account cap** (`"free user run limit reached"`). A new key on the same account hits the same cap. | **Blocker.** Fix = plan/account, not code. |
| 2 | "Apollo off + Apify only says *completed* but nothing ran" | **Status-rollup bug**: a run whose only active engine FAILED is reported `completed` when a *stale* candidate from a prior run exists. | **Confirmed bug.** Masks every failure. |
| 3 | "The AI has started hallucinating the titles" | **Confirmed & reproduced.** The dev doubled the single Strategist LLM call's output (added `focusTitle` + full `apolloPlan`), degrading quality on hard inputs. No sanitizer catches the bad output. | **Confirmed.** Fixable. |

Everything below is evidenced from the actual run (`pipeline 6a609e6b…`, job `Senior Inhouse Consultant SAP-CO/PS`, 2026-07-22 10:58).

---

## Problem 1 — the Apify "rate limit" is really the free-plan cap

**Stored error, verbatim** (`job.apifySearchError`):
> *"Apify refused the search: **'free user run limit reached'**. The run 'succeeded' but returned no data — this is an account/billing limit, NOT an empty candidate pool. Do not treat it as 'no candidates found'."*

- **Why changing the API key didn't help:** `"free user run limit reached"` is an **account/plan** limit on the Apify (HarvestAPI) side, not a per-key rate limit. A different key on the **same Apify account** shares the same exhausted free-tier run budget. Only a genuinely separate account, or a paid plan, resets it.
- **It's not new and not the merge's fault:** the same error killed the **SAP Payroll** run on 2026-07-19 (before the push). The developer's code actually *handles it correctly at the engine level* — it detects the quota markers and stores an honest, explicit message. The problem is upstream (billing) and downstream (the rollup hides it — Problem 2).
- **`autoQuerySegmentation` is NOT implicated in this run.** This run's `maxItems = 5`, and the new lever only triggers at `>25` ([apify_search_service.py](../../BE/app/services/apify_search_service.py)). So the cap was reached by cumulative test volume, not by the new recall lever. (It *is* a latent risk: at large `maxItems` it can multiply Apify consumption — worth a spend guard later, but it did not cause today's failure.)

**Implication for the client:** the free Apify/HarvestAPI tier cannot sustain real usage. This is the single hardest blocker and it is a **procurement decision, not a code fix** — a paid HarvestAPI/Apify plan (or dedicated account) is required before Castle Personal runs real searches. You noted Apify data is cleaner than Apollo; that makes this the priority, because right now the cleaner engine is the one that's capped.

---

## Problem 2 — the rollup masks a failed run as "completed" (confirmed bug)

**Location:** [candidate_pipeline.py:1872-1879](../../BE/app/services/candidate_pipeline.py#L1872-L1879)

```python
apify_ok  = (not run_apify)  or (kept.get("apify")  is not None)
apollo_ok = (not run_apollo) or (kept.get("apollo") is not None)
if not apify_ok and not apollo_ok:   rollup = "failed"
elif total > 0:                      rollup = "completed"
else:                                rollup = "awaiting_input"
```

Trace for today's run (Apify on, Apollo **off**, Apify **failed**):
- `apify_ok` → `False` (it failed).
- `apollo_ok` → **`True`** — because `not run_apollo` is `True` (a *disabled* engine is treated as "ok").
- So `not apify_ok and not apollo_ok` → `False` → **never rolls up to "failed"**.
- `total > 0` → **`True`**, but that `total` is the job's **cumulative** candidate count, which still holds **1 stale candidate** (Akif Cosgun) from an *earlier* Apollo run. → **`rollup = "completed"`**.

**Two distinct flaws:**
- **(A)** A skipped engine counts as "ok", so when only one engine runs and it fails, the run can never report `failed`.
- **(B)** "Did this run succeed?" is judged by the job's *total* candidate count (includes prior runs), not by *what this run added*. A failed re-run is masked by leftovers.

**This is exactly your confusion:** you turned Apollo off, ran Apify, it failed on the free cap — and the header said *"completed · 1 candidate"*. The truth (`apifySearchStatus: failed`, `apifySearchError: free user run limit reached`) was recorded per-engine but hidden by the rollup.

---

## Problem 3 — AI title hallucination (confirmed, with mechanism)

**The stored AI output for "Senior Inhouse Consultant SAP-CO/PS"** (`job.lastDiscoverFilters` / `lastApolloFilters` / `lastDiscoverAnchor`):

| Field | What the AI produced | Why it's wrong |
|---|---|---|
| Apify `searchQuery` | `"Senior Inhouse Consultant SAP-CO/PS"` | The **verbatim posting title** — the prompt explicitly bans this ("a SHORT fuzzy phrase, NOT the full title string"). It is the documented #1 zero-result cause. |
| Apify `currentJobTitles` | `["Senior Inhouse Consultant SAP-CO/PS", "SAP Consultant CO", "Senior consultant CO/PS", "SAP CO", "SAP PS"]` | Degenerate: the posting title again, plus 2-token fragments `"SAP CO"`/`"SAP PS"` that aren't real headlines. **No FICO expansion** (CO≈FICO in practice — the prompt teaches exactly this), **no German variants** (`Berater`, `SAP FICO Berater`) despite an obviously German "Inhouse" role. |
| Apollo `titles` | `["Senior Inhouse Consultant SAP-CO/PS"]` | A single non-real title — no family, no word-order variants. Apollo OR-expands, so one title = near-zero reach. |
| Apollo `locations` | `["Kolenz,Germany"]` | **Typo — "Kolenz" ≠ "Koblenz"** (the Apify field spelled it correctly). A misspelled location = 0 Apollo results. |
| `domainAnchor.coreTerms` | `["inhouse"]` | "inhouse" is a staffing-arrangement word, **not a specialty**. As the domain guard's core term it both rejects the real SAP-CO titles (none contain "inhouse") and lets any "Inhouse Consultant" of any field through. |

**Mechanism (why it "started" hallucinating):** the developer's change added `focusTitle` **and** the entire `apolloPlan` (titles, qKeywords, locations, seniorities) to the **same single structured LLM call** ([strategist.py diff](../../BE/app/services/sourcing/strategist.py)). The model now has to emit roughly **twice** the structured content in one shot. The **"Koblenz" (Apify) vs "Kolenz" (Apollo)** inconsistency *inside one call* is the tell-tale: the model is restating the same fact in two shapes and fumbling the copy. On a hard input (ambiguous 2-letter abbreviations `CO`/`PS`, two different modules fused, German "Inhouse"), that extra load tips it into low-quality output.

**Compounding:** the prompt is good, but there is **no sanitizer clamp** for the two worst failure modes — `searchQuery` being the full title, and degenerate `<=2-token` titles pass straight through ([strategist.py `_sanitize`](../../BE/app/services/sourcing/strategist.py) validates the ladder, anchor and Apollo caps, but never the primary title/query quality).

---

## What the developer got RIGHT — keep

- **The case study doc** ([SOURCING_INPUT_CASE_STUDY.md](SOURCING_INPUT_CASE_STUDY.md)) is genuinely good — accurate on both APIs, correctly identifies the enum-filter recall trap and the `person_industries[]` bug.
- **Enum filters default to Any** (prompt rule 4) — correct and recall-protecting; keep.
- **Two-engine architecture + cross-engine dedup by normalized LinkedIn URL** ([candidate_pipeline.py:1669](../../BE/app/services/candidate_pipeline.py#L1669)) — sound design; keep.
- **Per-engine sub-status + error fields** (`apifySearchStatus`, `apolloSearchStatus`, `apifySearchError`, `apolloKept`…) — good telemetry; the data is right, only the rollup that reads it is wrong.
- **Honest quota detection** — the engine correctly distinguishes "billing limit" from "no candidates". Keep and surface it.
- **Fixed `person_industries[]`** in the *new* Apollo search path (case study §B2). ✅ (Note: the **legacy** `apollo_service.search_candidates` at line 229 still sends it — dead-ish path via rerun, low priority.)

---

## Friction points — eliminate / fix before the client

| Item | Severity | Keep / Fix / Eliminate |
|---|---|---|
| Apify free-tier cap blocks the cleaner engine | **Blocker** | **Fix (procurement):** paid HarvestAPI/Apify plan or dedicated account. No code fixes this. |
| Rollup reports `completed` on a failed single-engine run | **High** | **Fix:** judge success by *this run's* additions, and treat a failed sole engine as `failed`; surface the quota message in the UI as a distinct state. |
| Title hallucination on hard inputs | **High** | **Fix:** (a) split the Strategist into two calls — Apify plan, then Apollo plan — or a second cheap "critique/repair" pass; **and** (b) add `_sanitize` clamps: reject `searchQuery` == full title, drop `<=2-token` titles, force FICO-style pairing & language variants. |
| Stale candidates make a re-run look successful | **Medium** | **Fix:** track per-run added-count; consider scoping the candidate list to the latest run or labeling run-of-origin. |
| Apollo data is messier than Apify (garbled location "Ruhr, 4720 Kelmis, Belgium"; masked contacts) | **Medium** | **Decision:** you prefer Apify. Recommend Apify-primary, Apollo as fallback/contact-reveal — not co-equal by default. |
| Legacy `person_industries[]` in `search_candidates` | **Low** | **Eliminate** when touching that path. |
| `autoQuerySegmentation` uncapped spend at high `maxItems` | **Low (latent)** | **Guard:** cap or cost-meter before it matters at paid scale. |

---

## Recommended sequence (on approval — nothing done yet)

1. **Unblock Apify** (you/procurement): move HarvestAPI off the free tier. Everything else is secondary to this, since the engine you trust is the one being capped.
2. **Fix the rollup** (small, surgical): a failed sole-engine run reports `failed`; success = "this run added candidates"; the UI shows "LinkedIn search hit its plan limit — not an empty result" as its own state.
3. **Fix the Strategist quality** (contained): split Apify/Apollo into separate calls (or add a repair pass) + add the two missing `_sanitize` clamps. Re-test on the *hard* inputs specifically (SAP-CO/PS, SAP-HCM, German "Inhouse" roles), not just the easy ones.
4. **Apify-primary policy** + stale-run hygiene.
5. **Cleanups:** legacy `person_industries[]`, `autoQuerySegmentation` spend guard.

Give me the go-ahead on any subset and I'll implement it (with tests + a live re-run to verify), same as the prior changes.
