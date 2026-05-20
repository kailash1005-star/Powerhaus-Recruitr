"""
Naukri Job Scraper — Firecrawl (Single File Test Script)
=========================================================
Tests both approaches:
  Mode A: rawHtml + BeautifulSoup (fast, 1 credit/page)
  Mode B: JSON mode / LLM extraction (resilient, 5 credits/page)

Usage:
    pip install firecrawl-py beautifulsoup4

    export FIRECRAWL_API_KEY=fc-your-key

    python test_scraper.py
"""

import os
import csv
import time
import json
import logging
from bs4 import BeautifulSoup
from firecrawl import Firecrawl

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

FIRECRAWL_API_KEY = "fc-a5218360c4624ed9b764dc0305c9d0ba"

SEARCH_URL = (
    "https://www.naukri.com/software-development-software-testing-python-java-software-engineer-jobs-in-chennai"
    "?k=software%20development%2C%20software%20testing%2C%20python%2C%20java%2C%20software%20engineer"
    "&l=chennai&experience=0&jobAge=1"
)

BASE_URL = "https://www.naukri.com"

# How many job detail pages to fetch (set to 0 to skip, None for all)
MAX_DESCRIPTION_FETCH = 3

# ── Firecrawl client ──────────────────────────────────────────────────────────

firecrawl = Firecrawl(api_key=FIRECRAWL_API_KEY)


# ══════════════════════════════════════════════════════════════════════════════
# MODE A — rawHtml + BeautifulSoup  (fast, cheap)
# ══════════════════════════════════════════════════════════════════════════════

def scrape_listings_html(url: str) -> list[dict]:
    """Fetch search results page as raw HTML, parse with BeautifulSoup."""
    logger.info("[Mode A] Scraping listings page with rawHtml...")
    t0 = time.time()

    result = firecrawl.scrape(url, formats=["rawHtml"])
    raw_html = getattr(result, "raw_html", None) or getattr(result, "rawHtml", "") or ""

    logger.info(f"[Mode A] Page fetched in {time.time() - t0:.2f}s | HTML length: {len(raw_html)} chars")

    soup = BeautifulSoup(raw_html, "html.parser")
    jobs = []

    job_wrappers = soup.find_all("div", class_="srp-jobtuple-wrapper")
    logger.info(f"[Mode A] Found {len(job_wrappers)} job wrappers in HTML")

    for job in job_wrappers:
        try:
            title_tag = job.find("a", class_="title")
            title = title_tag.text.strip() if title_tag else None
            job_url = title_tag["href"] if title_tag else None
            if job_url and not job_url.startswith("http"):
                job_url = BASE_URL + job_url

            company_tag = job.find("a", class_="comp-name")
            company = company_tag.text.strip() if company_tag else None

            salary_tag = job.find("span", class_="sal-wrap")
            salary = salary_tag.text.strip() if salary_tag else "Not Disclosed"

            location_tag = job.find("span", class_="loc-wrap")
            location = location_tag.text.strip() if location_tag else None

            skills = [s.text.strip() for s in job.find_all("li", class_="tag-li")]

            jobs.append({
                "title": title,
                "company": company,
                "salary": salary,
                "location": location,
                "skills": skills,
                "url": job_url,
            })
        except Exception as e:
            logger.warning(f"[Mode A] Error parsing a job card: {e}")

    logger.info(f"[Mode A] Parsed {len(jobs)} jobs in {time.time() - t0:.2f}s total")
    return jobs


def scrape_description_html(url: str) -> str:
    """Fetch individual job page and extract description via BeautifulSoup."""
    try:
        result = firecrawl.scrape(url, formats=["rawHtml"])
        raw_html = getattr(result, "raw_html", None) or getattr(result, "rawHtml", "") or ""
        soup = BeautifulSoup(raw_html, "html.parser")

        # Try known Naukri description container class patterns
        for cls in [
            "styles_JDC__dang-inner-html__h0K4t",
            "job-desc",
            "dang-inner-html",
        ]:
            tag = soup.find("div", class_=lambda c: c and cls in c)
            if tag:
                return tag.get_text(separator="\n").strip()

        # Fallback: grab all <section> text
        sections = soup.find_all("section")
        if sections:
            return "\n".join(s.get_text(separator="\n").strip() for s in sections)

        return "Description not found"
    except Exception as e:
        logger.error(f"[Mode A] Description fetch error for {url}: {e}")
        return "Error fetching description"


# ══════════════════════════════════════════════════════════════════════════════
# MODE B — JSON / LLM Extraction  (resilient, higher credit cost)
# ══════════════════════════════════════════════════════════════════════════════

LISTINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title":    {"type": "string"},
                    "company":  {"type": "string"},
                    "salary":   {"type": "string"},
                    "location": {"type": "string"},
                    "skills":   {"type": "array", "items": {"type": "string"}},
                    "url":      {"type": "string"},
                },
                "required": ["title", "company"]
            }
        }
    },
    "required": ["jobs"]
}

DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "role_summary":          {"type": "string"},
        "experience_required":   {"type": "string"},
        "required_skills":       {"type": "array", "items": {"type": "string"}},
        "description":           {"type": "string"},
    }
}


def scrape_listings_json(url: str) -> list[dict]:
    """Fetch search results page using Firecrawl JSON/LLM extraction."""
    logger.info("[Mode B] Scraping listings page with JSON/LLM extraction...")
    t0 = time.time()

    result = firecrawl.scrape(
        url,
        formats=[{
            "type": "json",
            "schema": LISTINGS_SCHEMA,
            "prompt": (
                "Extract every job listing on this Naukri search results page. "
                "For each job include: title, company name, salary (default 'Not Disclosed' if missing), "
                "location, list of skill tags, and the full job detail URL."
            )
        }]
    )

    logger.info(f"[Mode B] Response received in {time.time() - t0:.2f}s")

    raw = getattr(result, "json", None) or {}
    if isinstance(raw, str):
        import json as _json
        try:
            raw = _json.loads(raw)
        except Exception:
            raw = {}

    jobs = raw.get("jobs", []) if isinstance(raw, dict) else []

    # Normalise to same dict shape as Mode A
    for job in jobs:
        if not job.get("salary"):
            job["salary"] = "Not Disclosed"
        if not job.get("skills"):
            job["skills"] = []

    logger.info(f"[Mode B] Extracted {len(jobs)} jobs in {time.time() - t0:.2f}s total")
    return jobs


def scrape_description_json(url: str) -> dict:
    """Fetch individual job page using JSON/LLM extraction."""
    try:
        result = firecrawl.scrape(
            url,
            formats=[{
                "type": "json",
                "schema": DESCRIPTION_SCHEMA,
                "prompt": (
                    "Extract the full job description, required skills, "
                    "experience required, and a brief role summary from this job posting page."
                )
            }]
        )
        raw = getattr(result, "json", None) or {}
        if isinstance(raw, str):
            import json as _json
            try:
                raw = _json.loads(raw)
            except Exception:
                raw = {}
        return raw if isinstance(raw, dict) else {}
    except Exception as e:
        logger.error(f"[Mode B] Description fetch error for {url}: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# Shared utilities
# ══════════════════════════════════════════════════════════════════════════════

def fetch_descriptions(jobs: list[dict], mode: str, limit: int = None) -> list[dict]:
    """Enrich job listings with descriptions. mode = 'html' or 'json'."""
    targets = jobs if limit is None else jobs[:limit]
    logger.info(f"Fetching descriptions for {len(targets)} jobs (mode={mode})...")

    for i, job in enumerate(targets):
        url = job.get("url")
        if not url:
            job["description"] = "No URL"
            continue

        logger.info(f"  [{i+1}/{len(targets)}] {job.get('title')} @ {job.get('company')}")
        t0 = time.time()

        if mode == "html":
            job["description"] = scrape_description_html(url)
        else:
            desc_data = scrape_description_json(url)
            job["description"] = desc_data.get("description", "")
            job["role_summary"] = desc_data.get("role_summary", "")
            job["experience_required"] = desc_data.get("experience_required", "")
            job["required_skills"] = desc_data.get("required_skills", [])

        logger.info(f"    Done in {time.time() - t0:.2f}s")

    return jobs


def print_jobs(jobs: list[dict], label: str):
    print(f"\n{'='*60}")
    print(f"  {label} — {len(jobs)} jobs found")
    print(f"{'='*60}")
    for i, job in enumerate(jobs, 1):
        print(f"\n[{i}] {job.get('title')} | {job.get('company')}")
        print(f"    Salary  : {job.get('salary')}")
        print(f"    Location: {job.get('location')}")
        print(f"    Skills  : {', '.join(job.get('skills', []))}")
        print(f"    URL     : {job.get('url', '')[:80]}")
        if job.get("description"):
            preview = str(job["description"])[:200].replace("\n", " ")
            print(f"    Desc    : {preview}...")


def save_csv(jobs: list[dict], filename: str):
    if not jobs:
        return
    all_keys = set()
    for j in jobs:
        all_keys.update(j.keys())
    fieldnames = ["title", "company", "salary", "location", "skills", "url",
                  "description", "role_summary", "experience_required", "required_skills"]
    fieldnames = [f for f in fieldnames if f in all_keys]

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            row = dict(job)
            if isinstance(row.get("skills"), list):
                row["skills"] = ", ".join(row["skills"])
            if isinstance(row.get("required_skills"), list):
                row["required_skills"] = ", ".join(row["required_skills"])
            writer.writerow(row)
    logger.info(f"Saved {len(jobs)} jobs → {filename}")


# ══════════════════════════════════════════════════════════════════════════════
# Main test runner
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "="*60)
    print("  NAUKRI SCRAPER — FIRECRAWL PERFORMANCE TEST")
    print("="*60)

    # ── Test Mode A: rawHtml ──────────────────────────────────────
    # print("\n▶ Running Mode A (rawHtml + BeautifulSoup)...")
    # t_a = time.time()
    # jobs_a = scrape_listings_html(SEARCH_URL)
    # print_jobs(jobs_a, "Mode A — Listings")

    # if MAX_DESCRIPTION_FETCH and jobs_a:
    #     fetch_descriptions(jobs_a, mode="html", limit=MAX_DESCRIPTION_FETCH)
    #     print_jobs(jobs_a[:MAX_DESCRIPTION_FETCH], "Mode A — With Descriptions")

    # elapsed_a = time.time() - t_a
    # save_csv(jobs_a, "results_mode_a.csv")

    # ── Test Mode B: JSON/LLM ─────────────────────────────────────
    print("\n▶ Running Mode B (JSON/LLM extraction)...")
    t_b = time.time()
    jobs_b = scrape_listings_json(SEARCH_URL)
    print_jobs(jobs_b, "Mode B — Listings")

    if MAX_DESCRIPTION_FETCH and jobs_b:
        fetch_descriptions(jobs_b, mode="json", limit=MAX_DESCRIPTION_FETCH)
        print_jobs(jobs_b[:MAX_DESCRIPTION_FETCH], "Mode B — With Descriptions")

    elapsed_b = time.time() - t_b
    save_csv(jobs_b, "results_mode_b.csv")

    # ── Summary ───────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  PERFORMANCE SUMMARY")
    print("="*60)
    # print(f"  Mode A (rawHtml)  : {len(jobs_a)} jobs | {elapsed_a:.1f}s | ~{1 + MAX_DESCRIPTION_FETCH} credits")
    print(f"  Mode B (JSON/LLM) : {len(jobs_b)} jobs | {elapsed_b:.1f}s | ~{5 + MAX_DESCRIPTION_FETCH * 5} credits")
    print(f"\n  CSVs saved: results_mode_a.csv, results_mode_b.csv")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()