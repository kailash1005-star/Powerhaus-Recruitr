"""
Direct scraper for LinkedIn's public Jobs Guest API.

Endpoint:
    GET https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search

This is the same endpoint LinkedIn's logged-out "guest" job search page calls,
and the same one JobSpy hits internally. It returns an HTML fragment (a list
of <li> job cards), NOT JSON — so we parse it with BeautifulSoup.

Filters configured below:
    - keywords  = "python"
    - location  = "Chennai, Tamil Nadu, India"
    - f_TPR     = r86400        -> posted in the last 24 hours (seconds)
    - f_WT      = 1             -> onsite only (2 = remote, 3 = hybrid)

Notes:
    - Results per page: 25 (LinkedIn's fixed page size for this endpoint)
    - Pagination param: `start`, incremented by 25 each request
    - LinkedIn typically rate-limits a single IP around the 10th page (~250 jobs)
    - A 429 response means you've been rate limited -> script stops gracefully
      and saves whatever was collected so far
    - hours_old (f_TPR) and easy_apply (f_AL) are mutually exclusive on LinkedIn;
      this script only uses f_TPR, so that's not a concern here
"""

import csv
import random
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

SEARCH_PARAMS = {
    "keywords": "python",
    "location": "Chennai, Tamil Nadu, India",
    "f_TPR": "r86400",   # last 24 hours
    "f_WT": "1",         # 1 = onsite, 2 = remote, 3 = hybrid
}

JOBS_PER_PAGE = 25
MAX_RESULTS = 250          # stop once we have this many (set higher/lower as needed)
MIN_DELAY = 3               # seconds
MAX_DELAY = 7               # seconds
OUTPUT_CSV = "linkedin_python_chennai_onsite_24h.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.linkedin.com/jobs/search",
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_job_card(card) -> dict:
    """Extract fields from a single <li> job card."""

    def text_of(selector):
        el = card.select_one(selector)
        return el.get_text(strip=True) if el else None

    title = text_of("h3.base-search-card__title")
    company = text_of("h4.base-search-card__subtitle")
    location = text_of("span.job-search-card__location")

    link_el = card.select_one("a.base-card__full-link")
    job_url = link_el["href"].split("?")[0] if link_el and link_el.has_attr("href") else None

    job_id = None
    if job_url:
        m = re.search(r"-(\d+)(?:\?|$)", job_url)
        if m:
            job_id = m.group(1)

    date_el = card.select_one("time.job-search-card__listdate") or card.select_one(
        "time.job-search-card__listdate--new"
    )
    date_posted = date_el["datetime"] if date_el and date_el.has_attr("datetime") else None

    salary = text_of("span.job-search-card__salary-info")

    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "date_posted": date_posted,
        "salary": salary,
        "job_url": job_url,
    }


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

def scrape_linkedin_jobs():
    session = requests.Session()
    session.headers.update(HEADERS)

    all_jobs = []
    seen_ids = set()
    start = 0

    while len(all_jobs) < MAX_RESULTS:
        params = {**SEARCH_PARAMS, "start": start}

        resp = session.get(BASE_URL, params=params, timeout=15)

        if resp.status_code == 429:
            print(f"[!] Rate limited (429) at start={start}. Stopping — "
                  f"returning {len(all_jobs)} jobs collected so far.")
            break

        if resp.status_code != 200:
            print(f"[!] Unexpected status {resp.status_code} at start={start}. Stopping.")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("li")

        if not cards:
            print(f"[i] No more job cards returned at start={start}. End of results.")
            break

        new_count = 0
        for card in cards:
            job = parse_job_card(card)
            if job["job_id"] and job["job_id"] not in seen_ids:
                seen_ids.add(job["job_id"])
                all_jobs.append(job)
                new_count += 1

        print(f"[+] start={start}: fetched {len(cards)} cards, {new_count} new "
              f"(total so far: {len(all_jobs)})")

        if new_count == 0:
            # Likely hit the end / duplicate page — LinkedIn sometimes repeats
            # the last page instead of returning empty.
            print("[i] No new jobs on this page. Stopping.")
            break

        start += JOBS_PER_PAGE

        # polite, randomized delay to avoid tripping the rate limiter
        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    return all_jobs[:MAX_RESULTS]


def save_to_csv(jobs, path):
    if not jobs:
        print("[!] No jobs to save.")
        return

    fieldnames = ["job_id", "title", "company", "location", "date_posted", "salary", "job_url"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_NONNUMERIC)
        writer.writeheader()
        for job in jobs:
            writer.writerow(job)

    print(f"[✓] Saved {len(jobs)} jobs to {path}")


if __name__ == "__main__":
    print(f"Starting scrape at {datetime.now(timezone.utc).isoformat()}")
    print(f"Filters: keywords='{SEARCH_PARAMS['keywords']}', "
          f"location='{SEARCH_PARAMS['location']}', "
          f"f_TPR='{SEARCH_PARAMS['f_TPR']}' (last 24h), "
          f"f_WT='{SEARCH_PARAMS['f_WT']}' (onsite)")

    jobs = scrape_linkedin_jobs()
    save_to_csv(jobs, OUTPUT_CSV)