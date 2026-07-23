"""
Naukri Job Scraper — Full Pagination with Firecrawl (Mode B only)
==================================================================
FIXES APPLIED:
  1. Replaced page.waitForLoadState('networkidle') → 'domcontentloaded'
     Naukri never reaches networkidle due to ads/trackers — this was the
     root cause of the "Execution timed out" error.
  2. Added try/except around the entire interact block so a single
     timeout gracefully falls back to URL-based pagination.
  3. Tightened page.waitForTimeout from 2000 → 1500 ms to stay within
     Firecrawl's default session timeout.
  4. Added a specific element wait after click so the code knows the
     page has actually loaded new content before extracting.
"""

import os
import csv
import time
import json
import logging

from firecrawl import Firecrawl

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

import os
# SECURITY: the previously hardcoded key was leaked/burned and MUST be rotated at
# the Firecrawl dashboard. Read from the environment now — never commit a key.
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")

SEARCH_URL = (
    "https://www.naukri.com/software-development-software-testing-python-java-software-engineer-jobs-in-chennai?k=software%20development%2C%20software%20testing%2C%20python%2C%20java%2C%20software%20engineer&l=chennai&nignbevent_src=jobsearchDeskGNB&experience=2&jobAge=1"
)

# Safety cap on pages — set None to scrape ALL pages
MAX_PAGES = 5

# Enrich with full job descriptions? (costs 5 Firecrawl credits each)
SCRAPE_DESCRIPTIONS = True

# How many job descriptions to fetch (None = all)
MAX_DESCRIPTIONS = 10

OUTPUT_CSV = "naukri_jobs_all_pages.csv"

# ── JSON extraction schema for job listings ───────────────────────────────────

LISTINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title":      {"type": "string"},
                    "company":    {"type": "string"},
                    "salary":     {"type": "string"},
                    "location":   {"type": "string"},
                    "experience": {"type": "string"},
                    "skills":     {"type": "array", "items": {"type": "string"}},
                    "url":        {"type": "string"},
                },
                "required": ["title", "company"]
            }
        },
        "current_page":  {"type": "integer"},
        "total_pages":   {"type": "integer"},
        "has_next_page": {"type": "boolean"},
    },
    "required": ["jobs"]
}

DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "role_summary":        {"type": "string"},
        "experience_required": {"type": "string"},
        "required_skills":     {"type": "array", "items": {"type": "string"}},
        "full_description":    {"type": "string"},
    }
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def safe_json(result) -> dict:
    """Extract .json from a Firecrawl Document object safely."""
    raw = getattr(result, "json", None) or getattr(result, "JSON", None) or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def safe_output(result) -> str:
    """Extract .output from an interact result."""
    return getattr(result, "output", None) or getattr(result, "stdout", None) or ""


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — Paginated listing collection via scrape + interact
# ══════════════════════════════════════════════════════════════════════════════

# ── FIX: Playwright code now uses 'domcontentloaded' instead of 'networkidle'
# ── and waits for a specific job-card element to confirm the page has loaded.
NEXT_PAGE_PLAYWRIGHT_CODE = """
const nextSelectors = [
    'a[class*="next"]',
    'span[class*="next"]',
    'button[class*="next"]',
    'a[title="Next"]',
    '[class*="pagination"] a:last-child',
    'li.next a',
    'a.fright',
];

let clicked = false;
for (const sel of nextSelectors) {
    const el = await page.$(sel);
    if (el) {
        await el.click();
        clicked = true;
        break;
    }
}

// Fallback: find any element whose visible text is 'Next'
if (!clicked) {
    const els = await page.$$('a, button, span');
    for (const el of els) {
        const text = (await el.textContent() || '').trim().toLowerCase();
        if (text === 'next' || text === '>' || text === '›') {
            await el.click();
            clicked = true;
            break;
        }
    }
}

// FIX: Use domcontentloaded — Naukri never reaches networkidle.
// Also wait for a job card to appear so we know content is ready.
if (clicked) {
    await page.waitForLoadState('domcontentloaded');
    try {
        // Wait up to 5 s for a job-card element (adjust selector if needed)
        await page.waitForSelector(
            '.jobTuple, .job-tuple, article[class*="job"], [class*="jobCard"]',
            { timeout: 5000 }
        );
    } catch (_) {
        // Selector may differ — fall through; a short hard wait acts as safety net
        await page.waitForTimeout(1500);
    }
}

const url = page.url();
JSON.stringify({ clicked, url });
"""


def collect_all_jobs(firecrawl: Firecrawl) -> list[dict]:
    """
    Opens a Firecrawl browser session on the search URL, extracts jobs
    page by page by clicking Next, returns a flat list of all job dicts.
    Falls back to URL-suffix pagination if interact times out or is unavailable.
    """
    all_jobs = []
    page_num = 1

    # ── Step 1: Initial scrape ────────────────────────────────────────────────
    log.info("Opening search page in Firecrawl session...")
    result = firecrawl.scrape(
        SEARCH_URL,
        formats=[{
            "type": "json",
            "schema": LISTINGS_SCHEMA,
            "prompt": (
                "Extract every job listing visible on this Naukri search results page. "
                "For each job extract: title, company, salary ('Not Disclosed' if missing), "
                "location, experience required, list of skill tags, and the full job URL. "
                "Also extract current_page number, total_pages if shown, "
                "and has_next_page (true if a Next button or next page link is visible)."
            )
        }]
    )

    # Get the scrape_id — needed to reuse the same browser session for interact
    scrape_id = None
    metadata = getattr(result, "metadata", None)
    if metadata:
        scrape_id = (
            getattr(metadata, "scrape_id", None)
            or getattr(metadata, "scrapeId", None)
        )

    data = safe_json(result)
    jobs_page = data.get("jobs", [])
    has_next = data.get("has_next_page", False)
    total_pages = data.get("total_pages", "?")

    log.info(
        f"Page {page_num}/{total_pages}: "
        f"extracted {len(jobs_page)} jobs | has_next={has_next}"
    )
    for j in jobs_page:
        j["page_number"] = page_num
    all_jobs.extend(jobs_page)

    if not scrape_id:
        log.warning(
            "No scrape_id returned — cannot use /interact for pagination.\n"
            "Falling back to URL-suffix pagination."
        )
        return _fallback_url_pagination(firecrawl, all_jobs, page_num, total_pages)

    # ── Step 2: Loop — click Next, extract, repeat ────────────────────────────
    while has_next:
        if MAX_PAGES and page_num >= MAX_PAGES:
            log.info(f"Reached MAX_PAGES={MAX_PAGES} cap. Stopping.")
            break

        page_num += 1
        log.info(f"Clicking Next → page {page_num}...")

        # ── FIX: Wrap interact in try/except so a timeout triggers URL fallback
        try:
            interact_result = firecrawl.interact(
                scrape_id,
                code=NEXT_PAGE_PLAYWRIGHT_CODE,
            )
        except Exception as e:
            log.warning(
                f"interact() failed on page {page_num}: {e}\n"
                "Switching to URL-suffix fallback for remaining pages."
            )
            return _fallback_url_pagination(
                firecrawl, all_jobs, page_num - 1, total_pages
            )

        # Parse navigation result
        nav_result = {}
        try:
            nav_result = json.loads(safe_output(interact_result) or "{}")
        except Exception:
            pass

        if not nav_result.get("clicked"):
            log.warning(
                f"Next button not found on page {page_num - 1}. Stopping pagination."
            )
            break

        log.info(f"Navigated to: {nav_result.get('url', '?')}")

        # Extract jobs from the newly loaded page
        try:
            extract_result = firecrawl.interact(
                scrape_id,
                prompt=(
                    "Extract every job listing on the current Naukri search results page. "
                    "Return a JSON object with: "
                    "jobs (array with title, company, salary, location, experience, skills, url), "
                    "current_page (integer), total_pages (integer if shown), "
                    "has_next_page (boolean — true if a Next button is present and not disabled)."
                ),
            )
        except Exception as e:
            log.warning(f"Extraction interact() failed: {e}. Falling back to URL pagination.")
            return _fallback_url_pagination(
                firecrawl, all_jobs, page_num - 1, total_pages
            )

        raw_output = safe_output(extract_result)
        try:
            page_data = json.loads(raw_output)
        except Exception:
            import re
            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            page_data = json.loads(match.group()) if match else {}

        jobs_page = page_data.get("jobs", [])
        has_next = page_data.get("has_next_page", False)

        log.info(
            f"Page {page_num}/{total_pages}: "
            f"extracted {len(jobs_page)} jobs | has_next={has_next}"
        )
        for j in jobs_page:
            j["page_number"] = page_num
        all_jobs.extend(jobs_page)

    # Clean up the browser session
    try:
        firecrawl.stop_interaction(scrape_id)
        log.info("Browser session closed.")
    except Exception:
        pass

    return all_jobs


def _fallback_url_pagination(
    firecrawl: Firecrawl, existing_jobs: list, start_page: int, total_pages
) -> list[dict]:
    """
    Fallback pagination using Naukri's URL page-suffix pattern.
    Page N → path-N?query  (e.g. ...jobs-in-chennai-2?k=...)
    """
    all_jobs = list(existing_jobs)
    base = SEARCH_URL

    if "?" in base:
        path, query = base.split("?", 1)
    else:
        path, query = base, ""

    max_p = MAX_PAGES or (int(total_pages) if str(total_pages).isdigit() else 10)

    for page_num in range(start_page + 1, max_p + 1):
        page_url = f"{path}-{page_num}?{query}" if query else f"{path}-{page_num}"
        log.info(f"[Fallback] Scraping page {page_num}: {page_url}")

        try:
            result = firecrawl.scrape(
                page_url,
                formats=[{
                    "type": "json",
                    "schema": LISTINGS_SCHEMA,
                    "prompt": (
                        "Extract every job listing on this Naukri search results page. "
                        "For each job: title, company, salary, location, experience, skills list, url. "
                        "Set has_next_page=true if a Next button exists and is not disabled."
                    )
                }]
            )
        except Exception as e:
            log.error(f"Scrape failed on page {page_num}: {e}. Stopping.")
            break

        data = safe_json(result)
        jobs_page = data.get("jobs", [])

        if not jobs_page:
            log.info(f"No jobs on page {page_num} — stopping.")
            break

        log.info(f"Page {page_num}: extracted {len(jobs_page)} jobs")
        for j in jobs_page:
            j["page_number"] = page_num
        all_jobs.extend(jobs_page)

        if not data.get("has_next_page", True):
            log.info("has_next_page=False — stopping.")
            break

        time.sleep(1.5)  # polite delay

    return all_jobs


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — Enrich with job descriptions
# ══════════════════════════════════════════════════════════════════════════════

def enrich_with_descriptions(firecrawl: Firecrawl, jobs: list[dict]) -> list[dict]:
    """Fetch full job description for each job using a separate Firecrawl scrape."""
    targets = jobs if MAX_DESCRIPTIONS is None else jobs[:MAX_DESCRIPTIONS]
    log.info(f"Enriching {len(targets)} jobs with descriptions...")

    for i, job in enumerate(targets):
        url = job.get("url")
        if not url:
            job["full_description"] = "No URL"
            continue

        log.info(f"  [{i+1}/{len(targets)}] {job.get('title')} @ {job.get('company')}")
        try:
            result = firecrawl.scrape(
                url,
                formats=[{
                    "type": "json",
                    "schema": DESCRIPTION_SCHEMA,
                    "prompt": (
                        "From this Naukri job posting page extract: "
                        "a short role_summary (2-3 sentences), "
                        "experience_required (e.g. '2-4 years'), "
                        "required_skills as a list, "
                        "and the full_description (complete job description text)."
                    )
                }]
            )
            desc = safe_json(result)
            job["full_description"]    = desc.get("full_description", "")
            job["role_summary"]        = desc.get("role_summary", "")
            job["experience_required"] = desc.get("experience_required", job.get("experience", ""))
            job["required_skills"]     = desc.get("required_skills", [])
        except Exception as e:
            log.error(f"    Failed: {e}")
            job["full_description"] = "Error fetching description"

        time.sleep(1.5)

    return jobs


# ══════════════════════════════════════════════════════════════════════════════
# Save + display
# ══════════════════════════════════════════════════════════════════════════════

def save_csv(jobs: list[dict], filename: str):
    if not jobs:
        log.warning("No jobs to save.")
        return

    fieldnames = [
        "page_number", "title", "company", "salary", "location",
        "experience", "skills", "url",
        "role_summary", "experience_required", "required_skills", "full_description"
    ]
    present = set()
    for j in jobs:
        present.update(j.keys())
    fieldnames = [f for f in fieldnames if f in present]

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            row = dict(job)
            for list_field in ("skills", "required_skills"):
                if isinstance(row.get(list_field), list):
                    row[list_field] = ", ".join(row[list_field])
            writer.writerow(row)

    log.info(f"Saved {len(jobs)} jobs → {filename}")


def print_summary(jobs: list[dict]):
    by_page = {}
    for j in jobs:
        p = j.get("page_number", 1)
        by_page.setdefault(p, []).append(j)

    print(f"\n{'='*60}")
    print(f"  SCRAPE COMPLETE — {len(jobs)} total jobs across {len(by_page)} pages")
    print(f"{'='*60}")
    for p in sorted(by_page):
        print(f"  Page {p}: {len(by_page[p])} jobs")
    print()
    print("Sample (first 3 jobs):")
    for job in jobs[:3]:
        print(f"  [{job.get('page_number')}] {job.get('title')} | {job.get('company')} | {job.get('location')}")
        print(f"       Skills: {', '.join(job.get('skills', []))}")
        if job.get("role_summary"):
            print(f"       Summary: {job['role_summary'][:120]}...")
        print()
    print(f"  CSV saved → {OUTPUT_CSV}")
    print(f"{'='*60}\n")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    firecrawl = Firecrawl(api_key=FIRECRAWL_API_KEY)

    t0 = time.time()
    all_jobs = collect_all_jobs(firecrawl)
    log.info(f"Stage 1 done — {len(all_jobs)} jobs in {time.time()-t0:.1f}s")

    if SCRAPE_DESCRIPTIONS and all_jobs:
        t1 = time.time()
        all_jobs = enrich_with_descriptions(firecrawl, all_jobs)
        log.info(f"Stage 2 done — descriptions fetched in {time.time()-t1:.1f}s")

    save_csv(all_jobs, OUTPUT_CSV)
    print_summary(all_jobs)


if __name__ == "__main__":
    main()