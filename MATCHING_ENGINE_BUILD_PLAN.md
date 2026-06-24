# Recruitr — Matching Engine Build Plan (Production-Grade, One-Go Execution)

## Goal
Given a **Job Description**, parse it, **match it against a corpus of candidate CVs**, and return the
**top 5 candidates to call/outreach, each with reasoning**. Backend on the existing **MongoDB**
(new collections — leave current ones untouched) + **Pinecone** for vector search.

## What exists vs. what's missing (verified)
- **Exists & reused:** FastAPI app, async Mongo (`motor`) in [database.py](BE/app/database.py),
  `jobs` collection with raw JD text, OpenAI structured-JSON extraction pattern in
  [openai_company_service.py](BE/app/services/openai_company_service.py), transparent scoring
  pattern `_score_match` in [candidate_pipeline.py](BE/app/services/candidate_pipeline.py).
- **Missing (this plan builds all of it):** CV ingestion/parsing, LLM JD parser, embeddings,
  Pinecone vector store, hybrid match + deterministic scoring + LLM reasoning, the top-5 API,
  config & deps.

---

## Architecture (data flow)
```
CV files ─▶ Docling(parse→markdown) ─┬▶ embed(markdown) ──────────▶ Pinecone upsert + Mongo store
                                     └▶ small-LLM extract(filter fields: years/location/skills)

[USER UPLOADS JD doc + presses "Run Matching" button]
JD doc  ─▶ Docling(parse) ─▶ small-LLM parse(requirements) ─▶ embed ─▶ Pinecone query (k + filters)
        └▶ candidate shortlist ─▶ deterministic score ─▶ LLM reasoning (top ~10) ─▶ TOP 5 + reasons
```
**Two-layer parsing (key decision):** **Docling** (MIT, local, free) does *document → clean
Markdown/tables/OCR* — it replaces `pypdf`/`python-docx`. A **small LLM** does only *meaning*:
a few hard-filter fields per CV + the final top-5 reasoning. Retrieval embeds Docling's Markdown
**directly**, so no LLM is needed for the bulk matching — big cost saving, better accuracy.

**Principle:** LLM does *light field extraction + reasoning only*; hard constraints (must-have
skills, min years, location) are deterministic code. Reproducible (temperature=0, pinned
model/prompt versions on records) and auditable.

**Trigger:** explicit, user-initiated. CVs are ingested up front (the dump). Matching runs **only
when the user uploads a JD/roles document and clicks "Run Matching"** → one orchestrating endpoint.

---

## 1. New MongoDB collections (separate from existing)
- **`cv_candidates`** — one doc per CV:
  - `_id`, `contentHash` (sha256 of file bytes — dedup, **unique index**), `sourceFileName`,
  - `rawText`, `profile{ fullName, email, phone, location, totalYears, currentTitle,
    skills[], titles[], education[], experience[{company,title,start,end,summary}], certifications[] }`,
  - `contact{ email, phone, linkedin }` (for outreach),
  - `embedding{ pineconeId, model, dim, version, embeddedText }`,
  - `status` (`parsed|embedded|failed`), `error`, `createdAt`, `updatedAt`.
- **`parsed_jds`** — one doc per JD (do **not** mutate existing `jobs`):
  - `_id`, `sourceJobId?`, `rawText`, `requirements{ title, mustHaveSkills[], niceToHaveSkills[],
    minYears, location, seniority, responsibilities[] }`, `embedding{...}`, `createdAt`.
- **`match_runs`** — one doc per match execution (audit + cache):
  - `_id`, `jdId`, `params{ topK, filters }`, `results[{ candidateId, score, subscores{},
    reasons[], contact }]`, `modelVersions{ extract, embed, reason }`, `createdAt`.
- **Indexes:** `cv_candidates.contentHash` (unique), `cv_candidates.status`,
  `cv_candidates.profile.skills`, `match_runs.jdId`, `parsed_jds.createdAt`. Add to the startup
  index routine in [database.py](BE/app/database.py).

## 2. Pinecone (vector store)
- **Serverless index** `recruitr-cv`, dimension = embedding model dim (OpenAI
  `text-embedding-3-small` = **1536**; use `-large`=3072 if higher accuracy needed), metric=cosine.
- **Namespace** `default` now (reserve namespace = tenantId for future multi-tenant).
- **Vector metadata** (for pre-filtering): `candidateId`, `totalYears`, `location`, `skills` (list).
- Upsert CV vectors; query with JD vector + `filter` on years/location; return `topK` ids.
- Mongo is source of truth; Pinecone holds vectors. Write Mongo → upsert Pinecone → set
  `status=embedded`. On Pinecone failure, leave `status=parsed` for a reconcile job.

## 3. New services (`BE/app/services/`)
- **`docling_parsing_service.py`** — bytes → clean Markdown via **Docling** (`DocumentConverter`),
  for PDF/DOCX/PPTX/TXT/images (OCR + tables). Replaces pypdf/python-docx. Runs locally, MIT.
  Heavy (torch + model weights) → run via background worker, not inline in a tiny function.
- **`llm_extraction_service.py`** — small LLM (e.g. `gpt-4o-mini`), reusing the OpenAI JSON
  pattern (temp=0, strict JSON, retries): `extract_filter_fields(markdown)` → {years, location,
  skills[]} for CVs, and `parse_jd(markdown)` → structured requirements. **Retrieval embeds the
  Docling Markdown directly — no LLM in the bulk path.**
- **`embedding_service.py`** — `embed_texts(list[str]) -> list[vec]` via OpenAI embeddings,
  **batched** (≤2048/req), returns model+dim; builds embed text = skills + experience summary
  (PII excluded from the embedded text).
- **`pinecone_service.py`** — init client, `upsert(items)`, `query(vector, topK, filter)`.
  Provider-swappable interface (so Atlas Vector Search could replace it later).
- **`matching_service.py`** — orchestrator:
  1. parse+embed JD (or load `parsed_jds`),
  2. Pinecone query (topK≈50) + metadata filters,
  3. load candidate docs from Mongo,
  4. **deterministic score** (must-have coverage %, years vs minYears, location match) →
     `(score, subscores, reasons)` mirroring `_score_match`,
  5. take top ~10, call LLM for **grounded reasoning** (why fit / gaps, cite CV evidence),
  6. return **top 5** with score + reasons + contact; persist a `match_runs` doc.

## 4. New API (`BE/app/api/v1/matching.py`, register in [router.py](BE/app/api/v1/router.py))
- `POST /api/v1/cv/upload` — multipart CV dump, 1..N files → Docling→embed→store (background,
  deduped by `contentHash`); returns a `batchId`. `GET /api/v1/cv/batch/{batchId}` for progress.
- **`POST /api/v1/match/run`** — **THE BUTTON ENDPOINT.** Multipart: a JD/roles document (PDF/DOCX/
  TXT) **or** raw `{jdText}`. Orchestrates in one call: Docling-parse JD → parse requirements →
  embed → Pinecone query (topK + filters) → deterministic score → LLM reasoning →
  **returns top 5 with score, reasons, contact** and persists a `match_runs` doc (`matchRunId`).
- `GET /api/v1/match/{matchRunId}` — fetch a prior result.
- `GET /api/v1/cv/{id}` — inspect a parsed profile (debug/QA).

**UI trigger:** an upload control + a **"Run Matching"** button that posts to `POST /match/run`
and renders the returned top 5. CV-dump upload is a separate earlier action.

## 5. Config & dependencies
- **`config.py`** add: `PINECONE_API_KEY`, `PINECONE_INDEX=recruitr-cv`, `EMBEDDING_MODEL`,
  `EMBEDDING_DIM`, `EXTRACTION_MODEL` (e.g. `gpt-4o-mini`), `REASONING_MODEL` (e.g. `gpt-4o`).
- **`requirements.txt`** add: `docling>=2.0.0` (pulls torch + model weights), `pinecone>=5.0.0`.
  (`openai` already present. No pypdf/python-docx — Docling covers them.)
- **`.env`** (gitignored): the two keys. **No secrets in code.**

## 6. Production-grade concerns (built in, not later)
- **Idempotency/dedup:** `contentHash` unique; re-upload is a no-op.
- **Determinism/audit:** temperature=0; store `modelVersions` + `embedding.version` on every record;
  `match_runs` reconstructs any ranking.
- **Resilience:** per-file try/except — one bad CV never fails the batch (`status=failed`+`error`);
  LLM/embedding retries with backoff (existing pattern).
- **Bias control:** protected attributes excluded from embedded text and from scoring; reasoning
  grounded in skills/experience only; human still decides (these are call/outreach suggestions).
- **Cost/throughput:** Docling does the bulk parsing for free; embeddings batched; LLM only for
  light field extraction + reasoning on top ~10. No LLM in the retrieval path.
- **Docling footprint:** torch + model weights (~hundreds of MB–1 GB, downloaded on first run);
  CPU inference ~seconds/doc. Run as a **background worker / dedicated container** with adequate
  RAM — do **not** put it inside a small serverless function. Pre-bake weights into the image.
- **Validation/security:** file type & size limits; reject non-PDF/DOCX/TXT.
- **Consistency:** Mongo-first then Pinecone; reconcile via `status`.
- **Observability:** structured logs + per-stage timings; token usage captured on records.

---

## Execution order (one-go, dependency-correct)
1. Deps (Docling, Pinecone) + config + `.env` keys; create Pinecone index (one-time script);
   warm Docling model weights.
2. Mongo models + startup indexes (`cv_candidates`, `parsed_jds`, `match_runs`).
3. `docling_parsing_service` → `embedding_service` → `pinecone_service` → `llm_extraction_service`.
4. `matching_service` (deterministic score + LLM reasoning).
5. `matching.py` API (`/cv/upload`, **`/match/run`** = button, `/match/{id}`) + router registration.
6. Seed CV dump + run one JD through the button; tune scoring weights.
7. Thin UI: CV-dump uploader + JD upload box + **"Run Matching"** button + top-5 results view in `UI/`.

## Verification (prove it end-to-end)
- **Ingest:** `POST /api/v1/cv/upload` 10–20 sample CVs → all `status=embedded`; Pinecone vector
  count matches; duplicates deduped.
- **Match:** `POST /api/v1/match` with a JD → returns **exactly 5** candidates, each with score,
  subscores, reasons citing CV evidence, and contact for outreach.
- **Determinism:** same JD+corpus twice → identical ranking/scores.
- **Filters:** a min-years / location filter measurably changes the set (hard constraint works).
- **Resilience:** upload one corrupt file → it’s flagged `failed`, batch still succeeds.
- **Audit:** `GET /api/v1/match/{id}` returns the persisted result with model versions.

## Out of scope (flag, don't silently skip)
- Multi-tenant isolation (namespace reserved, not enforced yet).
- Outreach send / scheduling (separate agents — top-5 output is the handoff point).
- Cross-encoder rerank (deterministic score is enough for v1; add later if precision needs it).
- Graph/skill ontology (Phase 2).
