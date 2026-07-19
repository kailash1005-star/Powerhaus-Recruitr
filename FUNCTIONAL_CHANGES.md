# Functional Changes — Candidate Pipeline Hardening (Review Document)

**Date:** 2026-07-18 · **Scope:** candidate sourcing + candidate/CV matching only (the flows your client uses) · **Basis:** findings F-04…F-32 in [AUDIT_CANDIDATE_PIPELINE.md](AUDIT_CANDIDATE_PIPELINE.md), plus FC-16 (requested directly, not from the audit).

**Explicitly NOT done (your instruction):** API key rotation / git-history purge (audit F-02). The key in `Script/` remains untouched.
**Deferred (needs your go-ahead, involves data migration):** tenant scoping (F-01), GDPR machinery (F-03), dedup identity migration (F-13).

Every change below is independent and individually revertible. Nothing was committed to git — the working tree holds all changes, so you can review with `git diff` and drop any single change with `git checkout -- <file>` plus the per-change notes below. Say "revert FC-N" and I'll restore exactly that behavior.

**Verification done:** 128/128 offline unit tests pass (including 15 new ones in `BE/tests/test_scoring_v5.py`); full `BE/app` package byte-compiles; `app.main` imports cleanly; UI passes `tsc --noEmit`. Two pre-existing async test failures were fixed by the pytest config (FC-15). Not verified here: live Apify/OpenAI/Mongo flows — this machine has no `.env`; run one discovery + one match against your dev database before shipping.

---

## FC-1 — JD/CV extraction fails loud, with schema-enforced output

**Was:** `_chat_json` returned `{}` after failed retries. A failed JD parse silently removed every checklist component and the final score became raw cosine×100 while the run reported success (audit F-06). LLM JSON was `json.loads`'d and stored unvalidated (F-12) — `minYears: "5+ Jahre"` could reach a `>=` comparison and crash scoring.

**Now** (`BE/app/services/llm_extraction_service.py`, rewritten):
- OpenAI **Structured Outputs** (`json_schema`, `strict: true`) for JD parse, CV parse, and judge — the model cannot return a shape we didn't define; refusals and `finish_reason: "length"` truncation are detected and treated as failures.
- Parsed JSON is validated through Pydantic boundary models (`JDRequirements`, `CvFields`, `JudgeResponse`) that coerce LLM sloppiness (`"5+ Jahre"` → `5.0`, string→list, null items dropped, absurd years rejected).
- `parse_jd`/`extract_cv_fields` raise `ExtractionError` after retries. Callers surface it: the CV match endpoint returns **502 "JD parsing failed — run not scored"** (`api/v1/matching.py`), a pipeline match run flips to `failed`, CV ingest marks that one file `failed`, and sourcing's brief degrades to the thin prefill exactly as before (`sourcing/brief.py` already caught exceptions).
- A JD longer than 400 chars that parses to **zero must-haves** stamps a visible `requirementsWarning` on the run (both engines) instead of silently shipping a similarity-only ranking.

**Revert:** restore `BE/app/services/llm_extraction_service.py` and the two `except llm_extraction.ExtractionError` blocks in `BE/app/api/v1/matching.py`, plus the `requirementsWarning` blocks in `matching_service._run_match_impl` and `pipeline_match_service._run_pipeline_match`.

## FC-2 — Hardened OpenAI client config

**Was:** SDK-default 600s timeout per request, SDK retries stacked on hand-rolled retries, no `max_tokens`, no seed, no fingerprint tracking.
**Now:** explicit `timeout=OPENAI_TIMEOUT_SECS` (default 60s), `max_retries=0` (our backoff owns retrying), `max_tokens` per call type, `seed=OPENAI_SEED` (default 42), and the `system_fingerprint` of each call is recorded so score drift between identical runs is attributable (OpenAI documents seed as best-effort). Fingerprint + seed are persisted in every match run's `modelVersions`.
**Config added** (`BE/app/config.py`): `OPENAI_TIMEOUT_SECS`, `OPENAI_SEED`.
**Revert:** same file as FC-1; delete the two config fields.

## FC-3 — Semantic score calibration (SCORING_VERSION → match-scoring-5)

**Was:** raw cosine went into the 50%-weight "Profile fit" component. Related JD↔profile pairs land ~0.2–0.5 with `text-embedding-3-small`, so a perfect candidate could barely reach half the points on the largest component; and the old normalisation had a 50-point discontinuity at exactly sim=0 (audit F-07).
**Now** (`BE/app/services/matching_service.py`): monotone, continuous affine calibration — `[0.10 … 0.60] → [0 … 1]` (`SIM_CALIBRATION_FLOOR/CEIL`, documented in code). Raw similarity is still stored on every breakdown (`similarity`), plus the calibrated value and the mapping (`similarityCalibrated`, `similarityCalibration`) for audit.
**Effect on numbers:** semantic subscores rise across the board (a 0.35 cosine now reads 50 instead of 35). Rankings are order-preserved for the semantic component; absolute scores shift, which is why `SCORING_VERSION` was bumped — stored runs name the rules that made them.
**Revert:** restore the old two-branch `sim_norm` line and set `SCORING_VERSION` back to `match-scoring-4`.

## FC-4 — Anchored-rubric LLM judge, blended into the score

**Was:** the LLM only wrote prose bullets; nothing quantified *why* one candidate scored high and another low beyond the checklist, and the prose model was free-form JSON.
**Now:** a judge stage (`judge_candidates` in `llm_extraction_service.py`) scores each reasoned candidate against an explicit **anchored rubric** (`FIT_RUBRIC`: 90-100 "Ready now" … 0-39 "Not a fit", each band described in evidence terms) with hard rules binding it to the deterministic scorer: any wholly-missing must-have caps the judge at ≤74, ≥90 requires every must-have fully evidenced, partial evidence may never be called "missing", every reason must cite concrete evidence. Output is strict-schema + Pydantic-clamped.
- **Blend** (`matching_service.apply_judge`): `final = (1-w)·deterministic + w·judgeFit`, w = `MATCH_JUDGE_WEIGHT` (default 0.35), then **re-capped by the must-have coverage ceiling** — prose can never lift a candidate past their evidenced skills. The full blend (judge score, verdict, weight, deterministic input, whether the ceiling cut it) is stored on `breakdown.judge` and shown in run entries (`judge`, `reasoning: "judge"`).
- CV engine judges the top `MATCH_REASON_TOP_N` in **one batched call**; pipeline engine judges per candidate as it streams (bounded by the candidate set, ~25-30 for your client's use case). Both metered by `cost_service`.
- Judge failure degrades to the deterministic score, visibly (`judge: null`, `reasoning: "deterministic"`) — never a hidden default.
**Config added:** `MATCH_JUDGE_ENABLED` (default true), `MATCH_JUDGE_WEIGHT` (0.35). **Set `MATCH_JUDGE_ENABLED=false` in `.env` to turn the whole stage off without a code revert.**
**Revert:** config flag first; full revert = restore the reasoning sections of both engines and remove `apply_judge`.

## FC-5 — The candidates table now shows the real score

**Was (audit F-05):** the table sorted on `candidate.matchScore` = a fixed 90/70/45/30 title-token heuristic written at sourcing time and never updated; the real scoring engine wrote only to `match_runs`.
**Now:**
- After a pipeline match run scores a candidate, the final score, reasons, `matchScoreSource: "match_run"`, `matchScoringVersion`, and `lastMatchRunId` are written back to the candidate document (`pipeline_match_service.py`).
- Sourcing-time docs are stamped `matchScoreSource: "sourcing_heuristic"` (`candidate_pipeline.py`, both builders).
- UI (`PipelineJobCandidatesPage.tsx`): the match badge uses the **shared band definition** and renders heuristic scores as *provisional* — dashed border, asterisk, tooltip "Provisional — title match only. Run Match for the real score."
**Revert:** remove the writeback block in `pipeline_match_service.py`, the `matchScoreSource` lines in `candidate_pipeline.py`, and restore the old `MatchBadge`.

## FC-6 — Rerun runs the same engine as discover

**Was (audit F-04 — the main "vague results" cause):** the rerun button invoked the legacy Apollo engine (country-only location, `include_similar_titles`, title shrink) while discover ran the Apify/LinkedIn agentic engine — one click silently replaced LinkedIn-grade results with country-wide noise.
**Now (`candidate_pipeline.py`):**
- `enqueue_job_discover` persists `lastDiscoverFilters/MaxItems/Hints/Ladder` on the job.
- `rerun_job_search` replays those stored filters through the **same agentic discovery** (with auto-broaden). Jobs that never had a discovery get Strategist-derived filters from the JD (one LLM call, no vendor spend). Apollo runs **only** when Apify is unconfigured or no filters are derivable, and the response + job document say which engine ran (`engine`, `jobs.$.searchEngine` stamped by both claim paths).
**Revert:** restore the original 34-line `rerun_job_search` and remove the `lastDiscover*` persistence + `searchEngine` stamps.

## FC-7 — profileLanguage questionnaire field un-deadened

**Was (audit F-17):** `DiscoverSchema.profileLanguage` (singular) was read by nothing — the recruiter's language choice never reached the actor.
**Now:** `_build_input` (`apify_search_service.py`) normalises singular→plural; `DiscoverSchema` also accepts `profileLanguages` (plural). Explicit plural wins over singular.
**Revert:** remove the 3-line normalisation and the schema field.

## FC-8 — Broadener can no longer drift unanchored

**Was (audit F-14):** the domain-anchor guard was empty when the initial search was `searchQuery`-only, so zero-result broadening ran unguarded — the one case where the LLM most plausibly wanders to a different profession.
**Now (`sourcing/broadener.py`):** when the first attempt carried no titles, the anchor falls back to the brief's job title (and the search query) with generic role words stripped. Signature is backward-compatible (`brief` optional) — existing tests unchanged and passing.
**Revert:** restore `_domain_anchor`/`_enforce_domain` to attempts-only.

## FC-9 — Per-candidate error isolation in both scoring loops

**Was (audit F-15):** one profile that raised inside scoring 500'd the whole CV run; in the pipeline engine an embedding error marked the entire run failed.
**Now:** both loops isolate per candidate. CV engine records failures in `analysis.errors` on the run; pipeline engine adds the candidate to `excluded` with the reason and keeps streaming.
**Revert:** unwrap the two try/excepts.

## FC-10 — Apify actor calls are time-bounded

**Was (audit F-10):** `.call(run_input=...)` waits indefinitely; a hung actor left the job `running` forever with a paid run leaked.
**Now:** a shared `call_actor_bounded` helper (`apify_profile_service.py`, used by both the search and profile-scraper calls) bounds the actor run and the client wait to `APIFY_CALL_TIMEOUT_SECS` (default 300s, +60s wait). A timed-out run surfaces as `ApifyRunFailed` → job `failed`, retryable.
**Version note:** the kwarg names differ across `apify-client` versions (3.x: `run_timeout`/`wait_duration` as `timedelta`; 1.x/2.x: `timeout_secs`/`wait_secs` as ints) and our pin is loose (`>=1.7.0`). The first cut used the older `timeout_secs` names and crashed at runtime on the installed 3.0.6 ("unexpected keyword argument 'timeout_secs'"). The helper now introspects the installed `.call()` signature and passes whichever names it accepts, so neither a version bump nor a downgrade can reintroduce an unbounded wait or crash the call.
**Revert:** remove `call_actor_bounded` + its two call sites and the config field.

## FC-11 — No more silent `except: pass` / misclassified duplicates

- Cost-metering failures in `apify_search_service.py` / `apify_profile_service.py` are now **logged** (still non-fatal — metering must not fail a paid call that succeeded).
- The Apollo insert path caught **bare `Exception`** and treated every write failure as "duplicate candidate"; it now catches `DuplicateKeyError` only — a real write error propagates to the run's failure handler instead of being mislabelled (audit F-22).
**Revert:** restore the bare excepts (not recommended).

## FC-12 — CV engine spend is metered

**Was (audit, cost §):** `run_match` had no `cost_context` — its embedding/extraction/reasoning spend landed as orphan events piggybacking on the next flow.
**Now:** the whole run is wrapped in `cost_context(STAGE_MATCHING, label=<jd file>, jobId=…)` via a thin `run_match` wrapper delegating to `_run_match_impl`.
**Revert:** collapse the wrapper back into one function.

## FC-13 — One score-band definition across the UI

**Was (audit F-20):** four different threshold sets (80/60/40, 75/60/40, 75/50, 70/55/40) — the same number read as different verdicts in different views.
**Now:** canonical bands are `BANDS` in `UI/components/matching/shared.tsx` (75/60/40). `ScoreBar`, `fitVerdict`, and the pipeline table's `MatchBadge` all derive from those cut points.
**Revert:** restore the local thresholds in each component.

## FC-14 — Startup reaper for orphaned runs

**Was (audit F-09):** a restart mid-run left `match_runs`/job statuses stuck on `running` forever; the UI polled a run no process was executing.
**Now:** `BE/app/services/run_reaper.py`, called at startup — anything still `running`/`queued`/`processing` whose last heartbeat is older than `STALE_RUN_REAP_MINUTES` (default 30) is flipped to `failed` with "Orphaned: the server restarted…". The age threshold makes it safe under multiple workers (a live run keeps refreshing `updatedAt`). This is the minimal durable-jobs step; Mongo lease-based claims/Cloud Tasks remain on the P1 roadmap.
**Revert:** delete `run_reaper.py` and its two-line call in `main.py`; remove the config field.

## FC-16 — Deep enrichment is human-controlled, not automatic (you requested this)

**Was:** discovery ran the LinkedIn search, stored the short profiles, then **automatically** deep-enriched every kept candidate via the paid Apify profile scraper (`_discover_candidates_for_job` ended with an auto-enrich block). The recruiter had no chance to review before that spend was committed.

**Now:**
- **Discovery stops after the short profiles.** `_discover_candidates_for_job` (`candidate_pipeline.py`) stores the search results (name, current title, company, location, photo — shown immediately) and sets the job's `enrichStatus` to `"ready"` (or `"none"` if nothing was found) with an `enrichReady` count. **No Apify profile scrape happens automatically.**
- **A human presses "Enrich all."** New toolbar button on the candidates page (`PipelineJobCandidatesPage.tsx`), visible whenever the job has candidates and enrichment isn't already running — independent of row selection. It enriches **every** candidate in the job together (full work history/skills/education), in the background, and the existing progress line reports it. Idempotent: already-enriched candidates are skipped, so it's safe to press again (the label becomes "Enrich new" after a completed run). The per-selection "Enrich (N)" button is unchanged.
- **Cheaper enrichment for discovered candidates.** New `_enrich_for_job` routes by candidate source: Apify-discovered candidates (whose `apolloId` is a LinkedIn URN) go straight to the Apify-only path instead of `bulk_enrich`'s Apollo→Apify — the Apollo /people/match stage could only ever fail for them and burned one paid Apollo call each (this also sidesteps audit F-23). Apollo-sourced pipelines still get both stages (verified email).
- **UI flow updated:** `pollDiscover` now treats search-complete as the end of discovery (it no longer waits for an auto-enrich that won't come) and prompts "Review them, then press Enrich all". The completion message shows profiles enriched / not-found.

**Note — Run Match still enriches on demand.** If you press **Run Match** on candidates that were never enriched, the match run auto-enriches them first (otherwise there's no profile to score). That is a separate, deliberate safety net (a match needs a profile), left in place. Tell me if you also want Run Match to refuse un-enriched candidates instead of enriching them.

**Config:** none added — uses existing `POST /pipelines/{id}/jobs/{jobId}/enrich` (already supported `candidateIds: null` = all).
**Revert:** restore the auto-enrich block in `_discover_candidates_for_job`, restore the old `_run_job_enrich` (drop `_enrich_for_job`), and in `PipelineJobCandidatesPage.tsx` remove the "Enrich all" button + `onEnrichAll` and restore `pollDiscover`'s old enrich-waiting branch. `bulkEnrichJobCandidates` accepting `null` is harmless to leave.

## FC-17 — Enrichment follows the selection (replaces "Enrich all")

**Was:** FC-16 added a whole-job "Enrich all" toolbar button next to a selection-scoped "Enrich (N)".
**Now:** one enrichment control, driven by the row selection (header checkbox = whole page). Both Enrich and Run Match stay visible whenever candidates exist — disabled with an explanatory tooltip until something is ticked — so the flow is discoverable without being able to fire accidentally.
**Files:** `UI/components/pages/PipelineJobCandidatesPage.tsx` (toolbar; `onEnrichAll` removed).
**Revert:** restore the FC-16 toolbar block.

## FC-18 — Review & edit must-have skills before every match

**Was:** Run Match fired immediately against whatever the JD parser extracted; the recruiter never saw the keywords their candidates were about to be scored on.
**Now:**
- Run Match opens a **"Check the matching criteria"** dialog: the extracted must-have and nice-to-have skills as removable chips + an add box, with the message that this is what the match scores against ("Is this enough?").
- Backend: new `GET /pipelines/{id}/jobs/{jobId}/requirements` (resolves the job's role spec, parsing the JD on first ask — cached by text hash after); `POST …/match` accepts `mustHaveSkills` / `niceToHaveSkills` / `minYears` overrides. `None` = keep parsed, empty list = deliberate edit.
- The edit is applied to that run, **stamped on the run** (`requirementsSource: "recruiter_edited"` — a stored score always names the requirements that made it), and **persisted onto the job's role spec** so sourcing and future matches use the recruiter's corrected list.
- Untouched list → no override sent, parsed provenance kept. Parse failure → the dialog degrades to empty lists with a visible warning and lets the recruiter type the keywords by hand (never a dead end).
**Files:** `BE/app/api/v1/pipelines.py`, `BE/app/services/pipeline_match_service.py`, `UI/lib/api.ts`, `UI/components/pages/PipelineJobCandidatesPage.tsx` (`SkillChips`, review modal).
**Revert:** remove the endpoint + schema fields + `requirements_override` plumbing; restore the old direct `onRunMatch`.

## FC-19 — Candidate phone number next to the LinkedIn button

**Was:** `candidate_merge` hard-set `contact.phone = None` even though the Apify profile scrape carries a `phone` field (populated when the member lists one publicly), so no phone ever reached the UI.
**Now:** the merge keeps the Apify phone; match-result cards show a tel: button with the number next to LinkedIn whenever one exists (nothing rendered when absent — most profiles don't list one). CV-sourced candidates already carried phones from CV extraction and now render the same way.
**Files:** `BE/app/services/candidate_merge.py`, `UI/components/matching/shared.tsx`.
**Note:** takes effect for profiles enriched (or re-enriched) after this change — already-stored enrichments keep their old `phone: null`.
**Revert:** restore `phone: None` in the merge and drop the button.

## FC-20 — "Open to work" candidates are highlighted

**Was:** LinkedIn's `openToWork` flag arrived in the raw Apify payload and died in `raw.apify` — nothing read it.
**Now:** the merge carries `openToWork` onto the profile, enrichment stamps it top-level on the candidate document, match entries expose it, and the UI badges it green ("Open to work — likely to respond") in the shortlist/match cards next to the name and in the candidates table next to the status pill.
**Files:** `BE/app/services/candidate_merge.py`, `candidate_enrichment.py`, `pipeline_match_service.py`, `UI/lib/api.ts`, `UI/components/matching/shared.tsx`, `PipelineJobCandidatesPage.tsx`.
**Note:** like FC-19, populated by enrichments run after this change.
**Revert:** remove the `openToWork` lines in the three BE files and the two badges.

## FC-21 — One-screen pipeline creation (removes the two-form wizard)

**Was:** "Create pipeline" opened form 1 (company) → form 2 (job search + separate manual-job sub-form) → close → find the job → open the search questionnaire. Repeated manual effort on every pipeline.
**Now:** a single consolidated form: company (name/domain/industry/location) + the role (title/location/JD paste) with one submit — **"Create & find candidates"** — that creates the pipeline, creates the role, attaches it, and lands the recruiter **directly on the AI-prefilled search questionnaire** (`?search=1` auto-open, Suggest-filters reads the JD they just pasted). Same objective, one screen, zero re-entry.
- Failure model: if the pipeline is created but the role step fails, the pipeline is kept, the company section locks, and the same button resumes from the role step — no orphaned pipelines, no "already exists" dead end.
- The old "search existing jobs" list is gone from creation (it was the friction point); jobs can still be attached to a pipeline from the pipeline detail page as before.
**Files:** `UI/components/CreatePipelineModal.tsx` (rewritten), `UI/components/pages/PipelinesPage.tsx` (navigate on create).
**Revert:** `git checkout` both files (the old two-step wizard is in git history).

## FC-22 — Per-row Reject/Enrich buttons removed (the Apollo path)

**Was:** every table row carried Reject + Enrich buttons; the row Enrich hit the Apollo-only single-candidate endpoint, which returns 502 for every Apify-discovered candidate (audit F-23) — a button that always failed for discovery pipelines.
**Now:** the ACTIONS column is gone; the row itself opens the candidate slide-out, which keeps Accept/Reject and Enrich. The slide-out's Enrich was **rerouted** through the job-level enrich endpoint, which routes by candidate source (FC-16's `_enrich_for_job`) — it now works for both Apollo- and Apify-sourced candidates instead of 502ing.
**Files:** `UI/components/pages/PipelineJobCandidatesPage.tsx` (column removed, `onEnrich` rerouted; `enrichCandidate` import dropped).
**Revert:** restore the ACTIONS column markup and the old `onEnrich`.

## FC-23 — Two-tier domain anchor: widening can never change the profession

**Was:** the Broadener's domain guard passed any proposed title sharing ONE word with the original titles. For "SAP HCM Consultant" the anchor was `{sap, hcm}` — so "SAP FICO Consultant" passed on "sap" (the platform brand every SAP profession shares). Observed in production: an SAP-HCM search returning SAP FI-CO / "SAP Application Manager" candidates.
**Now:** the anchor is two-tier. The Strategist declares `domainAnchor: {coreTerms, ecosystemTerms}` (core = "hcm/successfactors/payroll", ecosystem = "sap"); a title is in-domain only if it carries a **core** term — a platform brand alone never qualifies. Heuristic fallback when no declared anchor exists (known platform brands demoted; a brand-only domain like "SAP Consultant" keeps the brand as core, since it's the only signal). Anchor hygiene in `_sanitize`: generic role words stripped from core, an anchor that rejects most of the Strategist's own titles is rebuilt from them.
**Files:** `BE/app/services/sourcing/common.py` (ECOSYSTEM_TOKENS, GENERIC_ROLE_WORDS, `derive_anchor_terms`, `title_in_domain`), `models.py` (`DomainAnchor`, `SearchStrategy.domainAnchor`), `strategist.py` (prompt + `_sanitize`), `broadener.py` (`_domain_anchor`, `_enforce_domain`), `candidate_pipeline.py` + `api/v1/pipelines.py` + `UI` (persist/echo `domainAnchor`).
**Revert:** `git checkout` the sourcing module; the old single-tier `_domain_anchor` is in history. (Two old tests asserting the single-tier semantics were updated — see test files.)

## FC-24 — The Broadener's target is LOCKED (titles/query clamped in code)

**Was:** on zero results the Broadener could relax titles as its last resort ("generalise WITHIN the domain") — the prompt forbade profession changes but the model did it anyway, and the guard (FC-23's predecessor) let brand-sharing titles through.
**Now:** title relaxation is removed from the Broadener entirely. It may relax enums → companies → location → language, in that order; `lock_target()` clamps `currentJobTitles`/`searchQuery` of **every** decision (agent or planned-ladder fallback) to the initial attempt's values, so drift is structurally impossible, not just prompted away. The Strategist's ladder steps are also title-locked at generation time (`_sanitize`). Rationale: the Strategist now emits the full within-specialty synonym family up front (FC-26), so any further title change IS a change of target — the recruiter's decision (FC-27), never a fallback's.
**Files:** `BE/app/services/sourcing/broadener.py` (INSTRUCTIONS rewritten, `lock_target`, `next_attempt`), `strategist.py` (ladder locking).
**Revert:** `git checkout BE/app/services/sourcing/broadener.py`.

## FC-25 — Dual-channel discovery: title search + keyword search, merged

**Was:** one search page per attempt, filtered by titles. People whose headline is generic ("IT-Consultant at X") but whose profile screams the specialty were unreachable — the main reason LinkedIn's own search felt better.
**Now:** the initial attempt runs TWO actor pages: the title-filtered search plus a keyword-only search (`searchQuery` without title filters), merged and deduped by profile id. Each candidate records `sourceChannels`; a hit found by BOTH channels is corroborated (+5 rank bonus, capped 95); a keyword-only hit whose title can't evidence the role is **kept** (floor 30) instead of being dropped by the title-only prescreen — the channel itself is the evidence. Broadened retries rerun only the title channel (the keyword channel doesn't carry the filters being relaxed). Keyword-channel failure is non-fatal; title-channel failures keep their existing abort semantics (incl. the quota abort). Cost: ≤ $0.20 initial + $0.10/retry.
**Files:** `BE/app/services/candidate_pipeline.py` (`_run_search_channels`, `_keyword_channel_filters`, `_channel_screen_policy`, `sourceChannels` on docs), `models.py` (`SearchAttempt.channelCounts`).
**Revert:** call `_run_search` directly in `_search_with_broadening` and drop the policy hook.

## FC-26 — Sourcing heuristic score = prescreen relevance (real ranking)

**Was:** the provisional `matchScore` at discovery time was a fixed 90/70/45/30 token-overlap bucket against the search query; the table's default sort was effectively four buckets.
**Now:** the provisional score is the prescreen relevance score — continuous 0-100 against the ROLE (target titles + must-have skills), channel-aware (FC-25 floor/bonus), with `matchReasons` from the same verdict. Still `matchScoreSource: "sourcing_heuristic"` (dashed badge) until a real match run rescores. The Strategist also now emits the full within-specialty synonym family as `currentJobTitles` (local-language + product-name variants) — recall comes from more same-specialty names, not looser ones.
**Files:** `BE/app/services/candidate_pipeline.py` (`_store_profiles`), `strategist.py` (title-family prompt).
**Revert:** delete the `doc["matchScore"] = …` override in `_store_profiles` (falls back to `_apify_score`).

## FC-27 — Shortfall → recruiter-approved widening (adjacent titles are opt-in)

**Was:** a thin/zero result was just "No candidates matched" — or worse, silently padded by title drift.
**Now:** the Strategist emits `adjacentTitles` (neighbouring specialties, e.g. "HRIS Consultant" for SAP HCM) that are **never searched automatically**. When discovery keeps fewer than `SOURCING_TARGET_CANDIDATES` (default 10), the job carries a `searchShortfall` payload and the UI shows an amber "widen the search?" banner: the adjacent titles as toggle chips + **Search wider** (re-runs the same filters plus the picked titles) / **Edit the search** / **Keep as is**. Zero kept results end as `awaiting_input` (recruiter decides), not a dead "completed" empty table. A transparency panel ("How this search ran") shows every attempt, per-channel hit counts, and who the prescreen dropped and why; table rows carry the evidence badges (`≈ matched title`, `2× found`, `Keyword find`).
**Files:** `BE/app/config.py` (`SOURCING_TARGET_CANDIDATES`), `candidate_pipeline.py` (shortfall + `awaiting_input`), `api/v1/pipelines.py` (schema passthrough), `UI/lib/api.ts`, `UI/components/pages/PipelineJobCandidatesPage.tsx` (banner, panel, badges), `UI/components/CandidateDiscoveryForm.tsx` (strategy card shows the reserve titles).
**Revert:** remove the shortfall block in `_discover_candidates_for_job` (restore plain `status="completed"`) and the banner/panel JSX.

## FC-28 — Enrichment capped at 10 per request (free-tier run budget)

**Was:** any number of candidates could be enriched at once; each block of 10 = one Apify actor run, and HarvestAPI's free tier allows ~20 runs total — one careless "enrich all 25" burned 3 of them.
**Now:** `JOB_ENRICH_SELECTION_MAX` (default 10 = `APIFY_ENRICH_BATCH`, i.e. exactly ONE actor run per click) enforced **server-side** (400 with a plain-language message) and mirrored in the UI: the Enrich button shows `(N/10)`, turns red and disables above 10. Second batches remain possible — deliberately, one run at a time.
**Files:** `BE/app/config.py`, `BE/app/api/v1/pipelines.py` (guard), `UI/components/pages/PipelineJobCandidatesPage.tsx` (`ENRICH_MAX`).
**Revert:** delete the guard + raise the config default.

## FC-29 — Skill evidence reads the whole profile (the "Marina" false negative)

**Was:** the deterministic skill matcher read only SHORT items — `skills[]`, current title, past titles. LinkedIn enrichment routinely returns `skills: []`, leaving people whose expertise lives in their experience bullet points invisible. Observed in production: a working SAP-HCM specialist (title "SAP-Spezialistin HCM", experience "Schwerpunkten SAP HCM PA, PY, OM…") scored **16.2/100** with reasons "No evidence of SAP-HCM experience despite current title as SAP-Spezialistin HCM" — all 7 must-haves called missing, every one of them evidenced in her profile.
**Now:** three matcher upgrades, each rule explained in the stored per-skill evidence:
- **Free-text tier** — profile summary + each experience entry (title+summary as one block) is evidence. Full credit on a contiguous phrase hit; 0.75 when all terms of a skill co-occur inside ONE entry; never fuzzy, never cross-entry scatter (tested).
- **All-terms rule** — "SAP-HCM" now credits against "SAP-Spezialistin HCM" (every term present in a short item, token-bounded).
- **Alias variants** — "Payroll (PY)" matches "Payroll" OR the module code "PY" as a standalone token ("PY" cannot fire inside "Python"; tested).
- Pool additionally reads headline + certifications. `SCORING_VERSION` bumped to `match-scoring-6`.
**Effect (live, same data):** Marina 16.2 → 46.5 deterministic; gaps 7 → 5 honest ones (semantic equivalences a string matcher must not guess — that's FC-30's job).
**Files:** `BE/app/services/matching_service.py` (`_skill_variants`, `_free_text_entries`, `_match_skill` free-text tier, `_skill_evidence_pool`, `_snippet_for`).
**Revert:** `git checkout BE/app/services/matching_service.py` (also reverts FC-30's `forced_credits`).

## FC-30 — QA auditor: the adversarial pass between scoring and completion

**Was:** the scoring chain had a structural blind spot. The judge's rubric makes the deterministic gap list AUTHORITATIVE (prose may never contradict the checklist — correct, that's what stops score inflation), which means **no component could ever catch a deterministic false negative**: the judge was *forced* to write "no evidence of SAP-HCM" over evidence it could see.
**Now:** every match run (pipeline + CV engines) is audited before completion by a QA agent whose incentive is **inverted by design** — its prompt states its only success metric is verified scorer mistakes; agreement earns nothing. Two mechanical referees stop the inverted incentive being gamed:
1. Every false-negative flag must include a **verbatim quote** from the candidate's evidence; the quote is string-checked (normalised) against the exact corpus the scorer reads. No quote match → flag discarded and counted against the auditor.
2. A verified flag never hand-edits a number — it replays `_score_candidate(forced_credits=…)`, so coverage → ceiling → score math stays the single source of truth and the evidence row reads `qa_verified` with the quote.
**Asymmetry (load-bearing):** verified false negative → score corrected UPWARD (candidate row updated, `matchScoreSource: "match_run_qa"`, the stale judge verdict dropped and archived in `qa.previousJudge`); false positive → **annotation only, never an automatic downgrade** — auto-rejecting on LLM say-so would manufacture the exact false negative this exists to prevent. Fail-open: auditor outage → run completes with `qa.status:"skipped"`, visibly un-audited.
**Live proof (run 6a5c9882…, real data, gpt-4o-mini):** 4 candidates reviewed · 13 flags raised · 11 quote-verified · **2 discarded by the referee** · 3 scores corrected — Marina 46.5→**93.2** (gaps 0), Pietro 50→85, Teresa 63.2→85; the genuinely-weaker candidate was left alone (no inflation), 0 false-positive flags.
**Files:** `BE/app/services/match_qa_service.py` (new), `pipeline_match_service.py` + `matching_service.py` (audit step before completion), `config.py` (`MATCH_QA_ENABLED`, `MATCH_QA_MODEL`).
**Revert:** set `MATCH_QA_ENABLED=false` (kill switch, no code change) or delete the audit blocks + service.

## FC-31 — Admin-only QA Reports page (run-wise false-detection metrics)

**Was:** no visibility into how often the system is wrong.
**Now:** every audit writes a `qa_reports` doc (per-run metrics: verdicts reviewed, FN flags raised/verified/discarded, corrections with from→to and the verified skills+quotes, FP annotations). Admin-only API (`GET /api/v1/qa/reports`, `/qa/reports/{id}`, probe `/qa/access`) gated by `ADMIN_EMAILS` (comma-separated, `BE/.env` — currently kailash@vanceltech.com; **add Sudharsan's email there**) or the `admin` role claim. UI: **QA Reports** page under the Monitor nav group — the nav item renders only after `/qa/access` confirms, so the beta client never sees the internal ledger exists; the backend 403s regardless.
**Files:** `BE/app/api/v1/qa.py` (new) + `router.py`, `UI/components/pages/QaReportsPage.tsx` (new), `UI/app/qa/page.tsx` (new), `UI/components/Sidebar.tsx` (gated item), `UI/lib/api.ts`, `UI/lib/i18n.tsx`.
**Revert:** remove the router include + the nav item.

## FC-32 — Deterministic location gate on discovery results (the Bavaria→India leak)

**Was:** the prescreen gate checked **title only** — it never looked at location. The keyword channel (FC-25) and the actor's own fuzzy location matching both let off-location profiles through, and nothing downstream filtered them: a search for "Bavaria, Germany" could surface a candidate in Bengaluru, India, straight to the recruiter.
**Now:** a deterministic gate runs over every result BEFORE it's shown ([location_resolver.py](BE/app/services/location_resolver.py) `location_verdict`, wired into `_store_profiles`). "Is 'Bengaluru, India' inside 'Bavaria, Germany'?" is exact arithmetic, so it is answered in code — no LLM, $0, cannot hallucinate:
- **Wrong country** → hard reject (`isAccepted:false`, `locationMismatch:true`, plain reason "Wanted Germany; candidate is in India"). Country aliases resolve (Deutschland≡Germany, USA≡United States).
- **Wrong region, right country** (Hamburg when Bavaria asked) → **kept and flagged**, not rejected — remote work and relocation are legitimate, so a hard reject there would be a false negative.
- **Location absent on either side** → kept (never reject on missing signal).
- Judged against the recruiter's ORIGINAL filter location, not the Broadener's (which may relax location as a last resort). Knob: `SOURCING_LOCATION_GATE` ("country" | "off").
**Live proof:** Bavaria vs Munich→match, vs Hamburg→region_mismatch (kept), vs Bengaluru→country_mismatch (rejected).
**Files:** `BE/app/services/location_resolver.py`, `candidate_pipeline.py` (`_store_profiles` gate + `requested_location`), `config.py`.
**Revert:** `SOURCING_LOCATION_GATE=off` (no code change).

## FC-33 — Sourcing-results auditor: does the search return the right *specialty*?

**Was:** nothing verified that the returned candidates were genuinely the specialty asked for. The keyword channel matches profile *text*, not the title line, so an "SAP FICO" (finance) or a generic "SAP Inhouse Consultant" could leak into an "SAP HCM" search.
**Now:** every discovery result set is audited before the recruiter acts ([sourcing_qa_service.py](BE/app/services/sourcing_qa_service.py), wired into `_discover_candidates_for_job`). Location is explicitly NOT its job (FC-32's exact gate already handled that) — this is the *fuzzy* question: is each kept candidate actually in the target specialty/seniority? It **flags, never deletes** — annotates the candidate row (`sourcingQaFlag`) and the admin report, because auto-removing on an LLM's say-so would re-create the false negative FC-29/30 exist to prevent. Confidence floor (0.6) — the model's own "unsure → don't flag" hedge made mechanical. Fail-open.
**The model decision (per the founder's question):** verification is harder than the job, so the auditors run a **stronger model than the workers**. Workers (extract/reason/judge) stay on `gpt-4o-mini`; both auditors default to **`gpt-4o`** via the shared `QA_AUDITOR_MODEL` knob (per-auditor overrides `MATCH_QA_MODEL` / `SOURCING_QA_MODEL`). Each auditor runs once per run over the whole batch, so the bigger model's cost is bounded. (An o-series reasoning model is a defensible post-beta upgrade but needs harness changes — no temperature/seed — so not tonight.)
**Live proof (real SAP-HCM run, gpt-4o):** 12 kept candidates → 11 genuine HCM specialists left untouched, the 1 ambiguous "SAP Inhouse Consultant" (no HCM in title) flagged at 80% as likely general SAP. Zero false positives on the real matches.
**Files:** `BE/app/services/sourcing_qa_service.py` (new), `candidate_pipeline.py` (`_audit_sourcing_results`), `config.py` (`SOURCING_QA_ENABLED`, `QA_AUDITOR_MODEL`, `SOURCING_QA_MODEL`), `api/v1/qa.py` + `UI` (QA page now shows both auditors, `kind` badge, sourcing metrics).
**Revert:** `SOURCING_QA_ENABLED=false` (no code change).

## FC-15 — Test infrastructure (support change, no runtime effect)

- `BE/pytest.ini` — registers `tests/`, `asyncio_mode = auto` (fixes 2 previously-failing async tests), and keeps the live-DB diagnostic scripts in `tests/` from being collected as tests.
- `BE/requirements-dev.txt` — pytest + pytest-asyncio (not in the production image).
- `BE/tests/test_scoring_v5.py` — 15 new tests: calibration continuity/monotonicity, judge blend + ceiling cap, boundary-model coercion/clamping, profileLanguage plumbing, broadener anchor fallback.
- Local env note: this machine had no Python; I installed Python 3.12 (user scope, winget) and created `BE/venv` per `run.ps1`'s expectation. No repo effect.

---

## Score semantics after this change-set (what to tell a client)

A candidate's score is now: **deterministic evidence blend** (calibrated whole-profile fit 50% · must-have skill coverage 30% · experience 12% · location 8%, weights renormalised when the JD doesn't state a component) **capped by must-have coverage** (missing skills set a hard ceiling), then **blended 65/35 with an anchored-rubric judge** that must cite evidence and can never out-vote the checklist upward. Every number on screen traces to a stored breakdown: per-skill evidence with the matching rule, the calibration inputs, the judge's verdict and weight, the model + seed + backend fingerprint that produced it, and the scoring version that computed it.
