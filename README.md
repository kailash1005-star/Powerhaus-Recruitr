# Powerhaus Recruitr

AI-driven candidate sourcing, enrichment, and JD-matching platform.
FastAPI + MongoDB backend, Next.js frontend, LinkedIn sourcing via Apify,
scoring + adversarial QA auditors on OpenAI models.

## Repo layout

| Path | What lives there |
|---|---|
| `BE/` | FastAPI backend. `app/api/v1/` routes → `app/services/` domain logic → `app/models/` schema docs. Real tests in `BE/tests/` (`pytest` from `BE/`; 220+ offline tests, no live DB needed). |
| `UI/` | Next.js frontend (BFF pattern — the browser never talks to the backend directly; `/api/proxy` forwards with the Auth0 bearer token). |
| `DB/` | `Mongo.txt` — MongoDB schema reference (cited by `BE/app/models/*`). |
| `docs/engineering/` | Setup + architecture docs: `AUTH0_SETUP.md`, `AUDIT_CANDIDATE_PIPELINE.md`, matching-engine build plan, agentic signal-detection design. |
| `docs/product/` | Product and expansion strategy. |
| `docs/sales/` | One-pagers, pitch deck, engine explainer (HTML/PDF). |
| `dump/` | **Quarantined dead code — never import from here.** See `dump/README.md`. Deletable at any time. |
| `FUNCTIONAL_CHANGES.md` | Running changelog of functional changes (FC-1…FC-33+). Append here for every behavior change. |
| `start-local.ps1` | Boots backend (:8000) + frontend (:3000) in separate windows. |
| `cloudbuild.yaml` | Cloud Build → Cloud Run deploy for the backend image. |

## Local development

```powershell
# prereqs: BE\.env and UI\.env.local filled in
.\start-local.ps1
```

Backend health: `http://127.0.0.1:8000/health` · API docs: `/docs` · UI: `http://localhost:3000`

**Gotcha that has bitten repeatedly:** `BE/.env` is read **once** at process
startup (`Settings()` in `app/config.py`). Editing it does nothing until you
fully stop and restart uvicorn — `--reload` does *not* re-read env vars.

## Tests

```powershell
cd BE
.\venv\Scripts\python.exe -m pytest       # offline; pytest.ini collects test_*.py only
```
