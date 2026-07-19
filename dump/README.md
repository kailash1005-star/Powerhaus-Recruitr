# dump/ — quarantined code, do not use

Nothing in this folder is imported, executed, deployed, or tested. It exists so
the working tree stays honest: everything outside `dump/` is live; everything
inside is dead weight kept only for archaeology. If you are ever tempted to
import from here — copy the file out, review it, and own it as new code.

Safe to delete this entire folder at any time; the product will not notice.

| Subfolder | What it is | Why it's here |
|---|---|---|
| `BE-scratch/` | 17 `scratch_*.py` one-off operational scripts (some DB-mutating, some calling billable APIs) + `apollo_samples/` payload dumps they write to | Were sitting in `BE/` root and shipped into the production Docker image (audit F-27). Never imported by the app. |
| `BE-oneoff/` | `backfill_pipeline_counts.py` | One-time data migration, already executed. |
| `BE-tests-diagnostics/` | `check_*.py`, `list_*.py`, `seed_*.py`, `debug_*.py`, `integration_*_live.py`, `sample_cv.html` | Live-DB diagnostic scripts that lived in `BE/tests/` but are not tests — `pytest.ini` already excluded them (`python_files = test_*.py`). Real tests stayed in `BE/tests/`. |
| `Script-legacy/` | Firecrawl/Naukri scrapers + CSV outputs + notebook | Pre-Recruitr experiments. ⚠️ `scraper.py`, `pagination.py`, `test.py` contain a **hardcoded Firecrawl API key** that is burned (in git history — audit F-02). Rotate it in the Firecrawl dashboard; moving these files does not un-leak it. |
| `root-misc/` | `guest.py`, `linkedin_python_chennai_onsite_24h.csv`, `index.html` (static demo page from `UI/`) | Repo-root strays with zero references. |
