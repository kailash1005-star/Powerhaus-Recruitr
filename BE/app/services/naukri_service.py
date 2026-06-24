"""
Naukri Background Service
Scrape jobs from Naukri using Firecrawl JSON mode → check uniqueness → filter/reject by title, experience, and location → store in jobs collection.
"""

import asyncio
import datetime as _dt
from datetime import datetime
from hashlib import sha1
import json
import logging
import re
import time
from typing import Any, Dict, List, Tuple

from bson import ObjectId
from firecrawl import Firecrawl

from app.config import settings


class ConsoleLogger:
    def __init__(self, name):
        self.logger = logging.getLogger(name)

    def info(self, msg, *args, **kwargs):
        formatted = msg % args if args else msg
        print(formatted, flush=True)
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        formatted = msg % args if args else msg
        print(f"WARNING: {formatted}", flush=True)
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        formatted = msg % args if args else msg
        print(f"ERROR: {formatted}", flush=True)
        self.logger.error(msg, *args, **kwargs)

log = ConsoleLogger(__name__)

# --- Structured JSON schemas for Firecrawl ---

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

# Playwright interactive pagination code
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

// Use domcontentloaded (Naukri never reaches networkidle)
if (clicked) {
    await page.waitForLoadState('domcontentloaded');
    try {
        await page.waitForSelector(
            '.jobTuple, .job-tuple, article[class*="job"], [class*="jobCard"]',
            { timeout: 5000 }
        );
    } catch (_) {
        await page.waitForTimeout(1500);
    }
}

const url = page.url();
JSON.stringify({ clicked, url });
"""

# --- Helpers ---

def _sanitize_for_mongo(value: Any) -> Any:
    """Recursively clean a value so it is safe for MongoDB insertion."""
    if isinstance(value, dict):
        return {str(k): _sanitize_for_mongo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_mongo(v) for v in value]
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    if isinstance(value, _dt.date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day)
    return value


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


def parse_experience_string(exp_str: str) -> Tuple[int | None, int | None]:
    """
    Parses experience string like '2-5 Yrs', '0-1 Yrs', '3 Yrs', '5 + yrs' into (min_exp, max_exp).
    """
    if not exp_str:
        return None, None
    exp_str = exp_str.lower().strip()
    
    numbers = [int(n) for n in re.findall(r'\d+', exp_str)]
    if not numbers:
        return None, None
        
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    
    num = numbers[0]
    if "+" in exp_str or "above" in exp_str or "more" in exp_str or "min" in exp_str:
        return num, None
    if "up to" in exp_str or "max" in exp_str:
        return 0, num
    return num, num


def evaluate_experience(
    job_min: int | None,
    job_max: int | None,
    min_exp: int | None,
    max_exp: int | None
) -> Tuple[bool, str]:
    """Evaluates job experience against candidate filters."""
    if min_exp is None and max_exp is None:
        return True, ""
        
    if job_min is None and job_max is None:
        return True, ""
        
    if max_exp is not None and job_min is not None and job_min > max_exp:
        return False, f"Job minimum experience ({job_min} yrs) exceeds candidate max experience ({max_exp} yrs)"
        
    if min_exp is not None and job_max is not None and job_max < min_exp:
        return False, f"Job maximum experience ({job_max} yrs) is less than candidate min experience ({min_exp} yrs)"
        
    return True, ""


def evaluate_location(job_location: str, search_locations: List[str]) -> Tuple[bool, str]:
    """Evaluates if job location matches target locations."""
    if not search_locations:
        return True, ""
    if not job_location:
        return False, "Job has no location"
    
    job_loc_lower = job_location.lower()
    for loc in search_locations:
        if loc.strip().lower() in job_loc_lower:
            return True, ""
            
    return False, f"Job location '{job_location}' does not match any search locations: {search_locations}"


def _build_job_dedupe_query(job: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Builds MongoDB query to detect duplicate jobs using the job URL."""
    job_url = str(job.get("url") or "").strip()
    if job_url:
        return {"boardName": "naukri", "jobDetails.jobUrl": job_url}, "job_url"

    fingerprint = "|".join([
        "naukri",
        str(job.get("title") or "").strip().lower(),
        str(job.get("company") or "").strip().lower(),
        str(job.get("location") or "").strip().lower(),
        str(job.get("page_number") or "1"),
    ])
    dedupe_key = sha1(fingerprint.encode("utf-8")).hexdigest()
    return {"boardName": "naukri", "jobDetails.dedupeKey": dedupe_key}, "fingerprint"


def _fallback_url_pagination(
    firecrawl: Firecrawl, search_url: str, existing_jobs: list, start_page: int, max_p: int
) -> List[dict]:
    """Fallback pagination using Naukri's URL page-suffix pattern."""
    all_jobs = list(existing_jobs)
    base = search_url

    if "?" in base:
        path, query = base.split("?", 1)
    else:
        path, query = base, ""

    for page_num in range(start_page + 1, max_p + 1):
        page_url = f"{path}-{page_num}?{query}" if query else f"{path}-{page_num}"
        log.info(f"[Naukri-Fallback] Scraping page {page_num}: {page_url}")

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

        time.sleep(1.5)

    return all_jobs


def collect_naukri_jobs(firecrawl: Firecrawl, search_url: str, max_pages: int) -> List[dict]:
    """
    Opens a Firecrawl browser session on the search URL, extracts jobs
    page by page by clicking Next, returns a flat list of all job dicts.
    """
    all_jobs = []
    page_num = 1

    log.info(f"[Naukri] Opening search page: {search_url}")
    try:
        result = firecrawl.scrape(
            search_url,
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
    except Exception as e:
        log.error(f"Initial scrape failed: {e}")
        return []

    scrape_id = None
    metadata = getattr(result, "metadata", None)
    if metadata:
        scrape_id = getattr(metadata, "scrape_id", None) or getattr(metadata, "scrapeId", None)

    data = safe_json(result)
    jobs_page = data.get("jobs", [])
    has_next = data.get("has_next_page", False)
    total_pages = data.get("total_pages", "?")

    log.info(f"Page {page_num}/{total_pages}: extracted {len(jobs_page)} jobs | has_next={has_next}")
    for j in jobs_page:
        j["page_number"] = page_num
    all_jobs.extend(jobs_page)

    if not scrape_id:
        log.warning("No scrape_id returned — falling back to URL-suffix pagination.")
        return _fallback_url_pagination(firecrawl, search_url, all_jobs, page_num, max_pages)

    while has_next:
        if max_pages and page_num >= max_pages:
            log.info(f"Reached max pages cap ({max_pages}). Stopping.")
            break

        page_num += 1
        log.info(f"Clicking Next → page {page_num}...")

        try:
            interact_result = firecrawl.interact(
                scrape_id,
                code=NEXT_PAGE_PLAYWRIGHT_CODE,
            )
        except Exception as e:
            log.warning(f"interact() failed on page {page_num}: {e}. Switching to URL fallback.")
            return _fallback_url_pagination(firecrawl, search_url, all_jobs, page_num - 1, max_pages)

        nav_result = {}
        try:
            nav_result = json.loads(safe_output(interact_result) or "{}")
        except Exception:
            pass

        if not nav_result.get("clicked"):
            log.warning(f"Next button not found on page {page_num - 1}. Stopping pagination.")
            break

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
            return _fallback_url_pagination(firecrawl, search_url, all_jobs, page_num - 1, max_pages)

        raw_output = safe_output(extract_result)
        try:
            page_data = json.loads(raw_output)
        except Exception:
            match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            page_data = json.loads(match.group()) if match else {}

        jobs_page = page_data.get("jobs", [])
        has_next = page_data.get("has_next_page", False)

        log.info(f"Page {page_num}/{total_pages}: extracted {len(jobs_page)} jobs | has_next={has_next}")
        for j in jobs_page:
            j["page_number"] = page_num
        all_jobs.extend(jobs_page)

    try:
        firecrawl.stop_interaction(scrape_id)
    except Exception:
        pass

    return all_jobs


def enrich_naukri_descriptions(firecrawl: Firecrawl, jobs: List[dict], max_desc: int) -> List[dict]:
    """Fetch full job description for each job using Firecrawl structured extraction."""
    targets = jobs if max_desc is None else jobs[:max_desc]
    log.info(f"Enriching {len(targets)} jobs with descriptions...")

    for i, job in enumerate(targets):
        url = job.get("url")
        if not url:
            job["description_data"] = {}
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
            job["description_data"] = {
                "description": desc.get("full_description", ""),
                "role_summary": desc.get("role_summary", ""),
                "experience_required": desc.get("experience_required", ""),
                "required_skills": desc.get("required_skills", [])
            }
        except Exception as e:
            log.error(f"Failed description fetch: {e}")
            job["description_data"] = {}

        time.sleep(1.5)

    return jobs


# --- Phase 1 Entrypoint ---

async def scrape_and_store_naukri_jobs(
    run_oid: ObjectId,
    run_config: Dict[str, Any],
    jobs_col,
) -> Dict[str, int]:
    """
    Scrapes jobs via Naukri Firecrawl Service, applies title, experience,
    and location rejection, checks for duplicates, and stores them.
    """
    api_key = settings.FIRECRAWL_API_KEY
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY configuration is not set.")

    firecrawl = Firecrawl(api_key=api_key)


    # Scrape all pages (typically capped at 100 on Naukri)
    max_pages = 100

    scrape_desc = run_config.get("scrapeDescriptions", True)
    max_desc = run_config.get("maxDescriptions", 10)

    min_candidate_exp = run_config.get("minExperience")
    max_candidate_exp = run_config.get("maxExperience")
    search_locations = run_config.get("searchLocations", [])

    # Compile target scraping URLs
    urls_to_scrape = []
    custom_url = run_config.get("searchUrl")
    if custom_url:
        urls_to_scrape.append((custom_url, "custom", "custom"))
    else:
        titles = run_config.get("searchTitles", [])
        locations = run_config.get("searchLocations", ["chennai"])
        for title in titles:
            for loc in locations:
                # Clean title and location for the slug format
                t_slug = re.sub(r'[^a-z0-9\s-]', '', title.lower())
                t_slug = re.sub(r'[\s-]+', '-', t_slug).strip('-')
                
                l_slug = re.sub(r'[^a-z0-9\s-]', '', loc.lower())
                l_slug = re.sub(r'[\s-]+', '-', l_slug).strip('-')
                
                import urllib.parse
                q_title = urllib.parse.quote(title)
                q_loc = urllib.parse.quote(loc)
                
                if l_slug:
                    url = f"https://www.naukri.com/{t_slug}-jobs-in-{l_slug}?k={q_title}&l={q_loc}"
                else:
                    url = f"https://www.naukri.com/{t_slug}-jobs?k={q_title}"
                urls_to_scrape.append((url, title, loc))

    raw_jobs = []
    for url, keyword, search_loc in urls_to_scrape:
        jobs = await asyncio.to_thread(collect_naukri_jobs, firecrawl, url, max_pages)
        for job in jobs:
            job["search_keyword"] = keyword
            job["search_location"] = search_loc
        raw_jobs.extend(jobs)

    # Optional description enrichment
    if scrape_desc and raw_jobs:
        raw_jobs = await asyncio.to_thread(enrich_naukri_descriptions, firecrawl, raw_jobs, max_desc)

    now = datetime.utcnow()
    total_scraped = len(raw_jobs)
    inserted = 0
    duplicates = 0
    accepted = 0
    rejected = 0

    for raw in raw_jobs:
        safe = _sanitize_for_mongo(raw)
        title = str(safe.get("title") or "").strip()
        if not title:
            continue

        desc_data = safe.get("description_data") or {}

        # 1. Experience Rejection Filter
        is_accepted = True
        reason = ""
        job_exp_str = desc_data.get("experience_required") or safe.get("experience") or ""
        j_min_exp, j_max_exp = parse_experience_string(job_exp_str)
        is_accepted, exp_reason = evaluate_experience(
            j_min_exp, j_max_exp, min_candidate_exp, max_candidate_exp
        )
        if not is_accepted:
            reason = exp_reason

        # 3. Location Rejection Filter
        if is_accepted:
            job_loc = safe.get("location") or ""
            is_accepted, loc_reason = evaluate_location(job_loc, search_locations)
            if not is_accepted:
                reason = loc_reason

        quality = "good" if is_accepted else "poor"
        rej_reason = None if is_accepted else reason

        if is_accepted:
            accepted += 1
        else:
            rejected += 1

        # Deduplication
        dedupe_query, dedupe_strategy = _build_job_dedupe_query(safe)

        # Cross-board deduplication check
        title_esc = re.escape(title)
        company_esc = re.escape(str(safe.get("company") or "").strip())
        existing = await jobs_col.find_one({
            "title": {"$regex": f"^{title_esc}$", "$options": "i"},
            "company": {"$regex": f"^{company_esc}$", "$options": "i"},
            "boardName": "naukri",
        }, {"_id": 1})

        if existing:
            duplicates += 1
            continue

        # Payload construction
        payload = {
            "runId": run_oid,
            "title": title,
            "company": str(safe.get("company") or "").strip(),
            "location": str(safe.get("location") or "").strip(),
            "boardName": "naukri",
            "externalId": "",
            "jobDetails": {
                "description": desc_data.get("description") or "",
                "requirements": desc_data.get("required_skills") or safe.get("skills") or [],
                "salary": {"raw": safe.get("salary") or "Not Disclosed"},
                "jobUrl": str(safe.get("url") or ""),
                "jobUrlDirect": str(safe.get("url") or ""),
                "companyUrl": "",
                "searchKeyword": str(safe.get("search_keyword") or ""),
                "searchLocation": str(safe.get("search_location") or ""),
                "dedupeStrategy": dedupe_strategy,
                "dedupeKey": sha1(str(dedupe_query).encode()).hexdigest() if dedupe_strategy == "fingerprint" else None,
                "rawPayload": safe,
            },
            "createdAt": now,
        }

        result = await jobs_col.update_one(
            dedupe_query,
            {
                "$setOnInsert": payload,
                "$set": {
                    "qualityStatus": quality,
                    "rejectionReason": rej_reason,
                    "updatedAt": now,
                },
            },
            upsert=True,
        )

        if result.upserted_id:
            inserted += 1
        else:
            duplicates += 1

    log.info(
        f"[Naukri] total={total_scraped}, inserted={inserted}, "
        f"duplicates={duplicates}, accepted={accepted}, rejected={rejected}"
    )

    return {
        "total_scraped": total_scraped,
        "inserted": inserted,
        "duplicates": duplicates,
        "accepted": accepted,
        "rejected": rejected,
    }
