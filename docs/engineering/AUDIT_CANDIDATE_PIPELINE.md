# Production-Readiness Audit — Candidate Sourcing & Matching Pipelines

**Product:** Powerhaus Recruitr (FastAPI + MongoDB backend, Next.js UI, Cloud Run)
**Scope:** Candidate sourcing pipeline (Apify/LinkedIn + Apollo), candidate matching/ranking pipeline, and every cross-cutting concern they depend on (auth, secrets, LLM infrastructure, observability, background jobs, data governance).
**Date:** 2026-07-18
**Status:** Audit only — no code has been changed. Every finding below was verified against the code with file:line evidence; every recommendation was cross-checked against publicly documented industry practice and regulation (sources cited inline).

---

## 1. Executive summary

The core of this product is **better than "vibe coded"** — the deterministic match scorer is explainable and versioned, every match run is persisted with a full per-candidate breakdown, cost tracking is genuinely well-engineered, and JWT verification is textbook-correct. Those are real assets.

But the product is **not sellable to an EU client today**, for three reasons that outrank everything else:

1. **Any authenticated user can read the entire database.** Auth0 login is verified correctly, but `tenant_id`/`sub` from the token are never used in a single MongoDB query. One recruiter account = full access to every pipeline, every scraped LinkedIn profile, every run, across all customers. This is the finding a client security review will fail you on. *(F-01)*
2. **A live API key is committed to the repo and its git history.** *(F-02)*
3. **The product stores scraped, non-consented LinkedIn PII with zero GDPR machinery** — no lawful-basis documentation, no retention limits, no erasure path, full verbatim third-party payloads stored per person. A French CNIL decision (KASPR, €200k fine) sanctioned exactly this data supply chain. Additionally, under the EU AI Act, AI-driven candidate sourcing/ranking is a **high-risk system (Annex III 4(a))** — the compliance deadline has moved to Dec 2027, but your client will demand readiness contractually. *(F-03, §6)*

The **accuracy complaint ("results are vague, don't match LinkedIn") has two identified root causes**, both confirmed in code:

- **The "rerun" button runs a different, older search engine than "discover."** Rerun silently invokes the legacy Apollo path — country-wide location, `include_similar_titles=true`, title-shrinking — which cannot reproduce a LinkedIn title search. Discover uses the newer agentic Apify/LinkedIn path. Two engines, one button-press apart, writing into the same collection. *(F-04)*
- **The score recruiters see and sort by is not the real score.** The candidates table displays `candidate.matchScore` — a throwaway title-token-overlap heuristic (fixed values 90/70/45/30) computed at sourcing time and never updated — while the real, audited scoring engine writes only to `match_runs`. *(F-05)*

A third accuracy landmine: **if the JD parse LLM call fails, the system silently returns `{}`**, all scoring components are dropped, and the final score quietly becomes raw embedding cosine × 100 with no error shown. The run looks successful. *(F-06)*

**Recommended sequence:** fix the 11 P0 items in §7 (secret rotation, tenancy, GDPR baseline, the two accuracy root causes, fail-loud parsing, durable jobs, webhook fail-closed, logging) before any client conversation; then the scoring architecture upgrade and eval harness in P1; then the AI Act conformity program in P2.

---

## 2. Method

- Three parallel deep code explorations (sourcing pipeline, matching pipeline, cross-cutting), followed by direct verification of every load-bearing claim against the source files.
- Remediation recommendations were validated against fetched public sources: EU AI Act texts and legal analyses, EDPB/CNIL/ICO enforcement and guidance, OpenAI production and structured-output documentation, Apify client documentation, MongoDB multi-tenant architecture documentation, Google Cloud Run background-work documentation, GitHub secret-removal documentation, and current retrieval/ranking literature (incl. candidate-matching-specific research). Citations inline.
- Legal items (§6) are engineering-grade summaries of the regulatory position, **not legal advice** — the DPIA/LIA work in P0 requires counsel.

---

## 3. Architecture as it stands

### 3.1 Sourcing — two parallel engines share one collection and one status field

**Engine A — Agentic Apify/LinkedIn path (current, reached via "discover"):**

```
POST /pipelines/{id}/jobs/{jobId}/suggest-filters      (pipelines.py:425)
  → build_brief (sourcing/brief.py:50)                 JD + role spec + recruiter hints
  → Strategist LLM (sourcing/strategist.py:161)        gpt-4o via pydantic-ai, typed output,
                                                       proposes titles/locations/enums/ladder
POST /pipelines/{id}/jobs/{jobId}/discover             (pipelines.py:454)
  → background asyncio task (candidate_pipeline.py:897)
  → deterministic filter mapping (apify_search_service.py:176)
  → Apify actor harvestapi/linkedin-profile-search, mode "Short" (~$0.10/page)
  → prescreen (prescreen_service, min score 25.0)
  → upsert on (pipelineId, apolloId)                   apolloId = LinkedIn URN here
  → zero results? Broadener LLM relaxes filters, ≤3 attempts (candidate_pipeline.py:818)
  → auto-enrich kept profiles: harvestapi/linkedin-profile-scraper ($0.004/profile),
    merged via candidate_merge, 30-day cache
```

**Engine B — Legacy Apollo path (reached via "rerun", candidate_pipeline.py:246→278→373):**
Apollo `people/search` with `person_titles[]`, `include_similar_titles=true`, location reduced to **country only** (`location_resolver.extract_country`), title shrunk to 3 tokens on low results, max 50.

**Crude score at insert:** `_apify_score` (candidate_pipeline.py:598-613) — title-token overlap against the search query, returns fixed 90/70/45/30. Stored as `candidate.matchScore`, never recomputed after enrichment.

### 3.2 Matching — two engines share one scorer; LLM extracts and explains, math decides

```
JD text → gpt-4o-mini (temp=0, json_object) → {title, mustHaveSkills, niceToHaveSkills,
                                               minYears, location, seniority, responsibilities}
          cached by JD-text hash (parsed_jds)

Candidate → embed (text-embedding-3-small) → cosine vs JD embedding
Score = renormalized weighted blend (matching_service.py:195-220, 444-459):
    semantic (raw cosine)  0.50
    skillCoverage          0.30    ← exact/containment/fuzzy skill matching
    experience             0.12
    location               0.08
  × 100, capped by must-have coverage ceiling: 100 / 85 / 65 / 50 / 40; floor 25 at zero coverage
LLM writes prose reasons only (name/gender/contact stripped — good bias control);
gaps come from the deterministic scorer and the LLM may not override them.
```

- **CV engine** (`matching_service.run_match`): JD vs uploaded-CV corpus, top-50 by cosine via a brute-force in-memory vector store.
- **Pipeline engine** (`pipeline_match_service.start_pipeline_match`): JD vs a job's Apify-enriched candidates; reuses the same `_score_candidate`, weights, and `SCORING_VERSION`.
- Every run creates a new `match_runs` document with full per-candidate breakdowns, weights, model versions, and scoring version — **this auditability is a real strength.**

### 3.3 What is already good (keep and build on)

| Asset | Evidence |
|---|---|
| JWT verification: RS256 pinned, audience/issuer checked, global router dependency, guarded kill-switch | `security/jwt_verifier.py`, `api/v1/router.py:24`, `startup_checks.py:68-96` |
| UI BFF proxy: token in httpOnly encrypted cookie, never in browser; strips auth headers; blocks path traversal; `no-store` | `UI/middleware.ts`, `UI/app/api/proxy/[...path]/route.ts` |
| Cost tracking: immutable events, versioned price book, context-var stage attribution across threads, ledger API | `services/cost_service.py`, `api/v1/cost.py` |
| Match-run persistence: per-candidate breakdown + `SCORING_VERSION` + weights + model versions, reruns never overwrite | `matching_service.py:190-191, 689` |
| Deterministic, explainable scoring core with must-have ceilings and LLM-proof gap authority | `matching_service.py:211-220, 639-646` |
| Quota-exhaustion detection that aborts broadening instead of burning retries | `apify_search_service.py:163`, `candidate_pipeline.py:855-867` |
| Bias control in reasoning prompt (no name/gender/age/contact sent to the LLM) | `llm_extraction_service.py:139-143` |
| Prompt-injected live enum vocabulary for the sourcing agents | `sourcing/models.py:199` |

---

## 4. Findings register

Severity: **S1** = ship-blocker for a client sale · **S2** = high, fix before scale · **S3** = medium · **S4** = hygiene.

### S1 — Critical

| ID | Finding | Evidence |
|---|---|---|
| **F-01** | **No tenant/row-level authorization.** `Principal.tenant_id`/`sub` exist (`security/deps.py:28-48`) but are used in **zero** queries — grep of `BE/app/api/v1` for `principal\|tenant_id\|Principal` returns no matches. `matching.py:129` `count_documents({})`, `runs.py:88` unfiltered `find()`. Any authenticated account reads all scraped PII across all customers. | verified |
| **F-02** | **Live Firecrawl API key committed**: `Script/scraper.py:125`, `Script/pagination.py:33`, `Script/test.py:29`, pasted into `AUTH0_SETUP.md:403`; present in git history (commits `d79f881`, `3a537f1`). `config.py:55-63` admits a prior key is "burned." | verified |
| **F-03** | **No GDPR machinery** for a scraped-PII product: no consent/LIA/DPIA artifacts, no retention policy (only TTL is the enrichment *cost cache*), no DSAR/erasure endpoints (only cascade deletes of pipelines/runs), full verbatim Apollo person payloads stored per candidate (`models/candidates.py:113-116` `enrichedRaw`), no action-level audit log. See §6.2 for the enforcement precedent. | verified |
| **F-04** | **Rerun runs the wrong engine.** `rerun_job_search` (`candidate_pipeline.py:246`) spawns `_search_candidates_for_job` (line 278) — the legacy **Apollo** engine (country-only location via `location_resolver.py:15-20`, `include_similar_titles=true` at `apollo_service.py:155,232`, title-shrink to 3 tokens at `apollo_service.py:47-72`) — while "discover" runs the Apify/LinkedIn engine. Direct root cause of "results don't match LinkedIn." | verified |
| **F-05** | **The UI's "Match" score is not the matching engine's score.** Candidates table sorts/filters on `candidate.matchScore` = `_apify_score` title-token overlap, fixed 90/70/45/30 (`candidate_pipeline.py:598-613`), computed at insert on the *short* profile, never re-scored after enrichment. The real score lives only in `match_runs`. Recruiters are ranking on a throwaway heuristic. | verified |
| **F-06** | **Silent scoring collapse on JD-parse failure.** `_chat_json` returns `{}` after retries (`llm_extraction_service.py:73`); empty `mustHaveSkills` → all components inapplicable → weight renormalization gives semantic 100% and ceiling 100 (`matching_service.py:446-458`). Final score = raw cosine × 100; run reports success; no error surfaced. | verified |

### S2 — High

| ID | Finding | Evidence |
|---|---|---|
| **F-07** | **Raw uncalibrated cosine is 50% of the score.** `text-embedding-3-small` cosine for related JD/CV pairs typically lands ~0.2–0.5, used raw as the dominant subscore → systematic compression; plus a normalization discontinuity at sim=0 (`matching_service.py:394`). UI relabels this as "Profile fit" with bands that correspond to no calibration. | agent-verified |
| **F-08** | **Retrieval cap:** only top-50 by raw cosine are ever scored (`MATCH_RETRIEVE_K=50`); a candidate strong on must-haves ranked 51st by cosine is invisible. Compounded by F-07. | agent-verified |
| **F-09** | **Background work is non-durable.** All sourcing/matching/enrichment runs are fire-and-forget `asyncio.create_task` / `BackgroundTasks` (`candidate_pipeline.py:278,564,685`, `pipeline_match_service.py:131`, `runs.py:62`). Cloud Run restart/scale-in mid-run ⇒ work silently lost, run stuck `"running"` forever; no reaper, no cancellation, no watchdog. Multi-worker (`UVICORN_WORKERS`) makes task state per-process. | verified pattern |
| **F-10** | **No timeout on Apify actor calls.** `client.actor(...).call(run_input=...)` (`apify_search_service.py:275`, `apify_profile_service.py:315`) waits indefinitely; a hung actor = job stuck `running` with paid run leaked. | agent-verified |
| **F-11** | **Webhooks fail open.** Smartlead/Cal.com signature verification returns `True` when the secret is unset (`outreach_provider.py:117-120, 255-258`); verification failure returns HTTP 200. | agent-verified |
| **F-12** | **No LLM output validation.** LLM JSON goes `json.loads` → raw dict → Mongo. `models/match_runs.py`/`parsed_jds.py` Pydantic models exist but validate nothing (and are stale vs. what services actually write). A string `minYears` can reach `>=` comparisons. | agent-verified |
| **F-13** | **Dedup identity collision.** `apolloId` holds an Apollo person-id for Apollo candidates and a LinkedIn URN for Apify candidates; unique index `(pipelineId, apolloId)` (`database.py:117`) ⇒ the same human sourced via both engines becomes two documents. Also makes correct GDPR erasure impossible. | agent-verified |
| **F-14** | **Broadener can drift when unanchored.** Domain-anchor guard (`sourcing/broadener.py:129-162`) anchors only on the first attempt's `currentJobTitles`; a `searchQuery`-only initial search leaves the anchor empty and relaxation runs unguarded. Broadened results are stored regardless. | agent-verified |
| **F-15** | **No per-candidate error isolation in scoring loops.** CV engine: one raising profile 500s the whole run (`matching_service.py:584-591`). Pipeline engine: `embed_text`+`_score_candidate` sit outside the per-candidate guard (`pipeline_match_service.py:292-297`) → whole run marked failed. | agent-verified |
| **F-16** | **No central LLM client.** Six services construct their own OpenAI clients with no explicit timeout (SDK default 600s/request), no `max_tokens`, hand-rolled `time.sleep` retries, no rate limiting. CV-engine matching spend bypasses `cost_service` entirely (orphan events). | agent-verified |

### S3 — Medium

| ID | Finding | Evidence |
|---|---|---|
| **F-17** | `profileLanguage` questionnaire field is dead: schema field is singular (`pipelines.py:141`), every consumer reads plural `profileLanguages` — the recruiter's language choice never reaches the actor. | verified |
| **F-18** | Nondeterminism unpinned: temp=0 without `seed`/`system_fingerprint` logging; extracted must-haves can drift between runs → rankings shift with no trace. | agent-verified |
| **F-19** | Extracted-but-unused signals: `niceToHaveSkills`, `seniority`, `responsibilities`, education, certifications are parsed, stored, and never scored. Mild double-counting: title feeds both embed text and skill evidence. | agent-verified |
| **F-20** | Four inconsistent score-band sets in the UI (80/60/40, 75/60/40, 75/50, 70/55/40) — the same number reads as different verdicts in different views. | agent-verified |
| **F-21** | Observability: 63 bare `print()` calls in app code, no logging config, no structured logs, no correlation IDs, no Sentry/OTel. | agent-verified |
| **F-22** | Silent exception swallowing: cost metering `except: pass` (`apify_search_service.py:302`, `apify_profile_service.py:374`); bare `except Exception` assumed to be duplicate-key (`candidate_pipeline.py:483`). | agent-verified |
| **F-23** | Manual single-candidate enrich endpoint 502s for every Apify-sourced candidate (sends LinkedIn URN to Apollo `/people/match`, `apollo_enrich.py:37-44`). | agent-verified |
| **F-24** | Tests cover auth only; no pytest config, no CI; several "tests" are live-DB diagnostic scripts; nothing tests tenancy, scoring, or actor parsing. | agent-verified |
| **F-25** | Cached JD parse may predate the recorded extraction model — `modelVersions.extract` records the *current* model while `requirements` may come from an older cached parse (`role_spec_service.py:95-109`); provenance misstated. | agent-verified |
| **F-26** | Auditability of provenance gaps aside, run status lifecycle docs are stale: state-machine docstring (`candidate_pipeline.py:9-24`) and `searchStatus` default `"queued"` no longer match reality (`awaiting_input`). | agent-verified |

### S4 — Hygiene

| ID | Finding |
|---|---|
| **F-27** | 17 `scratch_*.py` operational scripts (some DB-mutating, billable-API-calling) in `BE/` root, baked into the production image via `Dockerfile` `COPY . .`; no `.dockerignore` coverage. |
| **F-28** | `GrokCompanyService` actually calls Google Gemini (`grok_company_service.py:16-21`) — misleading name. |
| **F-29** | Duplicate scoring helpers (`_score_match` vs `_apify_score`), unused `_skill_present`, dead config lists (`INDUSTRY_PERSONA_MAP={}`), scraped-jobs CSV committed at repo root. |
| **F-30** | Pipeline recount runs ~3 count queries per job on every accept/reject click (`pipelines.py:747`) — O(jobs) per click. |
| **F-31** | Actor IDs and *human-readable mode strings* (`"Short"`, `"Profile details no email ($4 per 1k)"`) hardcoded as config defaults — a silent vendor rename breaks runs. |
| **F-32** | `download-profile` GET lets any authenticated user trigger a paid scrape of an arbitrary URL (`candidates.py:141-149`); upload endpoint has size cap but no content-type validation. |

---

## 5. Why the results feel "vague" — the accuracy chain, end to end

1. **Engine confusion (F-04).** A recruiter discovers (Apify/LinkedIn), then reruns — and silently gets Apollo's country-wide, similar-titles, shrunk-query results in the same list. Nothing in the UI distinguishes them.
2. **The visible score is noise (F-05).** Even when the deep scoring engine runs, the table still ranks by the insert-time 90/70/45/30 title heuristic.
3. **Silent degradation (F-06).** Any JD-parse failure quietly turns the "score" into raw cosine — which is itself compressed and uncalibrated (F-07).
4. **Recall ceiling (F-08)** hides skill-perfect candidates that phrase their profiles differently than the JD.
5. **Unanchored broadening (F-14)** can admit adjacent-but-wrong roles when the initial search was query-only.
6. **Dead filter (F-17)** ignores the recruiter's language preference entirely.

Every one of these has a bounded, verifiable fix (§7). None requires a rewrite.

---

## 6. Legal & governance position (EU client)

> Engineering summary with sources — commission a DPIA/LIA with counsel before the sale (that work is itself a P0 item).

### 6.1 EU AI Act — this product is a high-risk AI system

- **Annex III 4(a)** covers AI "intended to be used for the recruitment or selection of natural persons, in particular … to analyse and filter job applications, and to evaluate candidates" ([Annex III](https://artificialintelligenceact.eu/annex/3/)). LLM-driven sourcing plus automated candidate ranking is squarely in scope; Powerhaus is the **provider**, the client the **deployer**.
- **Deadline moved:** the Digital Omnibus (Parliament 16 Jun 2026, Council 29 Jun 2026) postpones stand-alone Annex III high-risk obligations from 2 Aug 2026 to **2 Dec 2027** ([Gibson Dunn](https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/), [DLA Piper](https://knowledge.dlapiper.com/dlapiperknowledge/globalemploymentlatestdevelopments/2026/The-Digital-AI-Omnibus-Proposed-deferral-of-high-risk-AI-obligations-under-the-AI-Act)). **Art. 50 transparency (2 Aug 2026) did not move** — if any surface is candidate-facing, AI-interaction disclosure is due *now*.
- **Provider obligations to build toward** (Arts. 8-17, [summary](https://artificialintelligenceact.eu/high-level-summary/)): risk management (9), data governance (10), technical documentation (11), **automatic event logging (12)** — the existing `match_runs` design is a genuine head start — instructions for use (13), human oversight (14: recruiters must be able to meaningfully override rankings; never auto-reject), accuracy/robustness (15), QMS (17), conformity assessment + EU database registration.

### 6.2 GDPR — applies today, with on-point enforcement precedent

- **KASPR (CNIL, €200k):** a service that scraped LinkedIn contacts into a database sold for prospecting/recruitment was fined for no valid Art. 6 basis, excessive retention (Art. 5(1)(e)), transparency failures (Arts. 12/14), and ignored access requests ([EDPB](https://www.edpb.europa.eu/news/news/2025/data-scraping-french-sa-fined-kaspr-eu200-000_en)). This is materially this product's data supply chain. The Clearview line (€30.5M NL fine, personal-liability probes) shows scraping-without-basis is systematically sanctioned ([AP](https://www.autoriteitpersoonsgegevens.nl/en/current/dutch-dpa-imposes-a-fine-on-clearview-because-of-illegal-data-collection-for-facial-recognition)).
- **Lawful basis:** Art. 6(1)(f) legitimate interest is realistic for professional-data recruitment sourcing, but EDPB scraping guidance requires a **documented, case-specific LIA** — and real data minimisation, which verbatim `enrichedRaw` payloads violate in spirit ([ReedSmith on EDPB guidelines](https://www.reedsmith.com/our-insights/blogs/technology-law-dispatch/102nbqu/edpb-web-scraping-guidelines-for-ai-making-the-impossible-possible/)).
- **Art. 14:** third-party-sourced data triggers a duty to inform data subjects within a month; the "disproportionate effort" exemption is narrow, must be documented, and even then demands a public privacy notice naming sources — KASPR was ordered to actively inform scraped individuals ([analysis](https://www.legiscope.com/blog/gdpr-article-14-third-party.html)).
- **Art. 22 / SCHUFA (C-634/21):** automated production of a probability value that a third party "draws strongly on" is itself an automated decision — the obligation attaches to the **score producer** ([IAPP](https://iapp.org/news/a/key-takeaways-from-the-cjeus-recent-automated-decision-making-rulings)). The match score is SCHUFA-shaped; mitigation is architectural (guaranteed human review, per-component reasoning — which `match_runs` already stores — and product-terms documentation).
- **DPIA is mandatory** before EU deployment (scoring/evaluation + innovative tech + subjects not informed; the ICO's audit of AI recruiting tools flagged missing DPIAs specifically — [ICO](https://ico.org.uk/about-the-ico/media-centre/news-and-blogs/2024/11/thinking-of-using-ai-to-assist-recruitment-our-key-data-protection-considerations/)).
- **Engineering consequences (P0 §7):** LIA+DPIA docs; public privacy notice; retention TTLs on non-engaged scraped profiles; DSAR export + Art. 17 erasure across `candidates`/`prospects`/`match_runs`/`enrichedRaw`; minimise `enrichedRaw` to used fields; action audit log. **Note: erasure cannot be implemented correctly until the identity-key collision (F-13) is fixed — that bug is a compliance blocker, not hygiene.**

### 6.3 LinkedIn ToS / supply-chain risk

hiQ v. LinkedIn ended with scraping public data likely *not* a CFAA violation, but hiQ **lost on breach of LinkedIn's User Agreement** and settled with a permanent injunction and destruction of scraped data ([ZwillGen](https://www.zwillgen.com/alternative-data/hiq-v-linkedin-wrapped-up-web-scraping-lessons-learned/)). Practical posture: this is a contract/supply-chain risk — LinkedIn kills scraping actors regularly, so Apify actor availability is a disclosed single point of failure; never contractually promise data-availability SLAs tied to LinkedIn.

---

## 7. Remediation plan (verified against published practice)

### P0 — before any client sale (dependency-ordered)

| # | Item | Fixes | Effort | Verified pattern / source |
|---|---|---|---|---|
| 1 | **Rotate Firecrawl key now**, purge history with `git-filter-repo --sensitive-data-removal`, force-push, contact GitHub to purge caches; add **gitleaks** pre-commit + CI diff scan | F-02 | S | [GitHub docs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository) — rotate *first*; history rewrite alone is insufficient |
| 2 | **Tenancy:** `tenantId` on every document; a thin repository layer that *requires* a `Principal` and injects the tenant filter (deny-by-default; handlers lose raw `db` access, enforced by CI grep); compound indexes with `tenantId` first; backfill migration (`"default"` → distinct tenant per client) | F-01 | M | [MongoDB multi-tenant architecture](https://www.mongodb.com/docs/atlas/build-multi-tenant-arch/) |
| 3 | **Authz test suite + CI:** two-tenant fixture; every endpoint asserts cross-tenant reads/writes fail; GitHub Actions running pytest + gitleaks as required checks | F-01, F-24 | S | standard |
| 4 | **Fail-loud JD parse:** OpenAI **Structured Outputs** (`json_schema`, `strict:true` — auto-converts Pydantic models; refusals programmatically detectable; check `finish_reason` for truncation) replacing `json_object`; on failure mark run `failed` visibly — never default `{}` | F-06, F-12 | S | [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs); pydantic-ai (already a dependency) for the same typed pattern |
| 5 | **Rerun-engine fix:** route rerun through the Strategist→Apify path; demote Apollo to an explicit, labeled fallback; stamp `engine` + `strategyVersion` on every run | F-04 | S | internal defect |
| 6 | **Identity/dedup fix:** dedicated person-identity key (LinkedIn URN canonical, Apollo id secondary), merge existing duplicates | F-13 | S/M | prerequisite for lawful erasure |
| 7 | **GDPR baseline:** DPIA + LIA (counsel); public privacy notice naming sources; retention policy + Mongo TTL indexes on non-engaged profiles; DSAR export + Art. 17 erasure endpoint spanning all person collections; minimise `enrichedRaw`; action audit log | F-03 | L | §6.2 sources (KASPR, EDPB, ICO) |
| 8 | **Webhooks fail closed:** `startup_checks.py` refuses production boot with unset webhook secrets; HMAC + timestamp-tolerance verification; 401 on failure | F-11 | S | [Svix webhook verification](https://docs.svix.com/receiving/verifying-payloads/why) |
| 9 | **Durable jobs (minimal):** Mongo-backed job claims — `findOneAndUpdate` lease + `leaseUntil`/`attempts`, heartbeat, **startup reaper** re-queuing expired leases; Apify calls get `timeout_secs`/`wait_secs` and persist the Apify run ID for orphan reconciliation; delete every `except: pass` | F-09, F-10, F-22 | M | [Cloud Run background-work guidance](https://cloud.google.com/blog/topics/developers-practitioners/use-cloud-run-always-cpu-allocation-background-work), [Apify client docs](https://docs.apify.com/api/client/python/reference/class/ActorClient) |
| 10 | **Structured JSON logging + Sentry:** stdout JSON (Cloud Run parses `severity`, trace correlation via `X-Cloud-Trace-Context`); contextvar `run_id`/`tenant_id` on every line; replace all 63 prints | F-21 | S/M | [Cloud Run logging](https://docs.cloud.google.com/run/docs/logging) |
| 11 | **`.dockerignore`** scratch scripts, tests, samples out of the image | F-27 | S | hygiene |

**The genuine sale-blockers are #2, #6, #7** (unscoped PII access; erasure currently impossible; no lawful-basis paperwork for a KASPR-shaped product). **#4 and #5 are the credibility blockers** — they are the two bugs producing the visible accuracy complaints. The rest are cheap enough that deferring buys nothing.

### P1 — hardening (first 1–2 months after P0)

- **Scoring v2** (fixes F-07/F-08/F-19, after characterization tests freeze current behavior):
  - Widen retrieval to top-150+; add lexical (Atlas Search/BM25) retrieval fused with dense via **Reciprocal Rank Fusion** — the industry default precisely because raw scores from different signals aren't comparable ([hybrid-search reference](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026)).
  - Keep the must-have ceilings and deterministic gates (they're good, explainable guardrails); **normalize the semantic component** (per-run min-max/z-score or logistic calibration); remove the sim=0 discontinuity.
  - **LLM-judge rerank of the top 20–30 only**, with an anchored rubric (per-level descriptors, calibrated on ~50 human-labeled JD-candidate pairs), Pydantic-typed verdicts, cost-bounded via `cost_service`. This is the published pattern for candidate-JD matching specifically — embedding retrieval + LLM rerank of top-k ([ConFit v3](https://arxiv.org/html/2605.09760v1); [LLM-as-judge practice](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)).
  - Score bands: one shared constant across all UI surfaces; per-component display (also Art. 14 oversight).
- **Sourcing eval harness:** ~20 golden recruiter briefs with known-good profiles; assert recall in top-25; run as a promptfoo/Braintrust/Langfuse-style regression suite in CI on any prompt/strategist change.
- **Central LLM gateway module:** explicit timeouts, tenacity backoff+jitter, `max_tokens` caps, `seed` + `system_fingerprint` logging (best-effort determinism per [OpenAI reproducibility docs](https://cookbook.openai.com/examples/reproducible_outputs_with_the_seed_parameter)), rate-limit awareness, per-run budget enforcement; meter the CV engine through `cost_service`. (LiteLLM if multi-provider routing ever becomes real; a thin wrapper is the right size today — [OpenAI production best practices](https://developers.openai.com/api/docs/guides/production-best-practices).)
- **Broadener guardrails:** non-empty domain anchor required before any relaxation (derive from JD title when query-only); log every relaxation step into the run record (feeds Art. 12).
- **Per-candidate error isolation** in both scoring loops; `errors: n` surfaced per run.
- **Cloud Tasks dispatch** for jobs if scale demands; DB-backed run-status polling replaces per-worker SSE affinity.
- **OTel GenAI semantic conventions** on the gateway (pin the semconv version — still stabilizing; [spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/)); provenance fix for cached parses (record the *producing* model, invalidate cache on model change — F-25).
- Fix F-17 (profileLanguage plumbing), F-23 (route manual enrich by source), F-20 (bands), F-28 (rename Gemini service).

### P2 — differentiation & AI Act program (start Q4 2026, complete before Dec 2027)

- AI Act conformity workstream: Art. 11 technical documentation, Art. 9 risk file, Art. 17 QMS-lite, EU database registration; formalize `match_runs` as the Art. 12 automatic event log (mostly documentation over telemetry you'll already have).
- Judge calibration set maintenance + **bias monitoring** across protected-characteristic proxies (the ICO's flagged failure mode for recruiting AI).
- Cross-encoder rerank stage and Atlas Search index at scale.
- Art. 50 transparency notices (pull forward to now if anything is candidate-facing — that deadline is 2 Aug 2026).

---

## 8. Target end state

After P0+P1, the honest pitch to your client is:

- **Isolated:** every query tenant-scoped at a layer that cannot be bypassed, proven by CI authorization tests.
- **Accurate and evaluated:** one sourcing engine with logged strategy; hybrid retrieval + calibrated scoring + rubric-anchored LLM rerank; a regression eval suite that gates every prompt change with recall/nDCG numbers you can show.
- **Honest under failure:** parse failures fail the run visibly; runs survive restarts via leases and a reaper; actors have timeouts and orphan reconciliation; per-candidate errors are counted, not fatal.
- **Governed:** documented LIA/DPIA, public privacy notice, retention TTLs, working DSAR/erasure, minimised payloads, an audit trail tying every score to model+prompt+operator — which doubles as your EU AI Act Art. 12 log, ahead of the Dec 2027 deadline.
- **Observable:** structured logs with correlation IDs, Sentry, GenAI-instrumented LLM spans, and a cost ledger you already have.

That combination — an *evaluated* matching engine plus *demonstrable* AI-Act/GDPR readiness — is precisely what generic AI-recruiting tools can't show in a procurement review, and it is achievable incrementally from the codebase you have.

---

*Appendix — verification notes: findings marked "verified" were confirmed by direct file reads during this audit (F-01, F-02, F-04, F-05, F-06, F-17, plus weights/ceilings at `matching_service.py:195-220`); "agent-verified" findings carry file:line evidence from the exploration passes over the same codebase snapshot. Legal deadlines reflect sources fetched 2026-07-17/18 and should be re-confirmed at contract time.*
