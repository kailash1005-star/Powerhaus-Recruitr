"""
JobSpy Background Service
Scrape jobs → check uniqueness → reject/accept by title → store in jobs collection.
"""

import asyncio
import datetime as _dt
from datetime import datetime
from hashlib import sha1
from math import isnan
from typing import Any, Dict, Tuple

from bson import ObjectId
from jobspy import scrape_jobs



REQUIRED_COLUMNS = [
    "id",
    "site",
    "job_url",
    "job_url_direct",
    "title",
    "company",
    "location",
    "date_posted",
    "emails",
    "description",
    "company_url",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    if isinstance(value, float) and isnan(value):
        return None
    return value


def _build_job_dedupe_query(job: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """
    Build a MongoDB query to detect duplicate jobs.

    Priority:
      1. boardName + externalId  (most reliable – platform-assigned id)
      2. boardName + jobUrl      (fallback – direct link)
      3. fingerprint hash        (last resort – composite of fields)
    """
    site = str(job.get("site") or "unknown").lower().strip()
    external_id = str(job.get("id") or "").strip()
    job_url = str(job.get("job_url") or job.get("job_url_direct") or "").strip()

    if external_id:
        return {"boardName": site, "externalId": external_id}, "external_id"

    if job_url:
        return {"boardName": site, "jobDetails.jobUrl": job_url}, "job_url"

    fingerprint = "|".join(
        [
            site,
            str(job.get("title") or "").strip().lower(),
            str(job.get("company") or "").strip().lower(),
            str(job.get("location") or "").strip().lower(),
            str(job.get("date_posted") or ""),
            str(job.get("search_keyword") or "").strip().lower(),
            str(job.get("search_location") or "").strip().lower(),
        ]
    )
    dedupe_key = sha1(fingerprint.encode("utf-8")).hexdigest()
    return {"boardName": site, "jobDetails.dedupeKey": dedupe_key}, "fingerprint"


def _build_cross_board_dedupe_query(job: Dict[str, Any]) -> Dict[str, Any] | None:
    """
    Build a cross-board duplicate check query.

    If title + company + date_posted are all present and all match an
    existing record, the job is a duplicate regardless of board or
    externalId.  Returns None when any of the three fields is missing.
    """
    title = str(job.get("title") or "").strip()
    company = str(job.get("company") or "").strip()
    date_posted = job.get("date_posted")

    if not title or not company or not date_posted:
        return None

    # Normalise date_posted to a datetime at midnight
    if isinstance(date_posted, str):
        try:
            date_posted = datetime.fromisoformat(date_posted)
        except (ValueError, TypeError):
            return None
    if isinstance(date_posted, _dt.date) and not isinstance(date_posted, datetime):
        date_posted = datetime(date_posted.year, date_posted.month, date_posted.day)

    # Match on case-insensitive title+company using regex, and exact date
    return {
        "title": {"$regex": f"^{_regex_escape(title)}$", "$options": "i"},
        "company": {"$regex": f"^{_regex_escape(company)}$", "$options": "i"},
        "createdAt": date_posted,
    }


def _regex_escape(s: str) -> str:
    """Escape special regex characters in a string."""
    import re
    return re.escape(s)


# ---------------------------------------------------------------------------
# Scraping  (1 title × 1 location × 1 site per call)
# ---------------------------------------------------------------------------

def _scrape_jobs(run_config: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Run JobSpy once per unique (title, location, site) combination."""
    titles = run_config.get("searchTitles", [])
    locations = run_config.get("searchLocations", [])
    sites = [s.lower().strip() for s in run_config.get("siteName", ["linkedin"]) if s]

    hours_old = int(run_config.get("hoursOld", 24))
    results_per_search = int(run_config.get("resultsPerSearch", 50))

    total = max(1, len(titles) * len(locations) * len(sites))
    index = 0
    collected: list[Dict[str, Any]] = []

    for location in locations:
        for title in titles:
            for site in sites:
                index += 1
                print(f"[JobSpy] [{index}/{total}] '{title}' in '{location}' on {site}")
                try:
                    jobs_df = scrape_jobs(
                        site_name=[site],
                        search_term=title,
                        location=location,
                        hours_old=hours_old,
                        results_wanted=results_per_search,
                    )
                    if jobs_df is None or jobs_df.empty:
                        print("  -> 0 results")
                        continue

                    available = [c for c in REQUIRED_COLUMNS if c in jobs_df.columns]
                    rows = jobs_df[available].to_dict(orient="records")
                    for row in rows:
                        row["search_keyword"] = title
                        row["search_location"] = location
                        row["search_site"] = site
                    collected.extend(rows)
                    print(f"  -> {len(rows)} results")
                except Exception as exc:
                    print(f"  !! JobSpy error for {title}/{location}/{site}: {exc}")

    print(f"[JobSpy] Total raw rows collected: {len(collected)}")
    return collected


# ---------------------------------------------------------------------------
# Phase 1 entry-point  (called by orchestrator)
# ---------------------------------------------------------------------------

async def scrape_and_store_jobs(
    run_oid: ObjectId,
    run_config: Dict[str, Any],
    jobs_col,
) -> Dict[str, int]:
    """
    Scrape jobs via JobSpy, apply title rejection, dedup, and store.

    Returns a stats dict:
      total_scraped, inserted, duplicates, accepted, rejected
    """
    raw_jobs = await asyncio.to_thread(_scrape_jobs, run_config)

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

        # All scraped jobs are accepted (title rejection removed)
        quality = "good"
        rej_reason = None
        accepted += 1

        # Dedupe
        dedupe_query, dedupe_strategy = _build_job_dedupe_query(safe)

        # Cross-board dedupe: title + company + date_posted
        cross_board_query = _build_cross_board_dedupe_query(safe)
        if cross_board_query:
            existing = await jobs_col.find_one(cross_board_query, {"_id": 1})
            if existing:
                duplicates += 1
                continue

        # Build payload — full raw payload kept in jobDetails.rawPayload
        company_url = str(safe.get("company_url") or "").strip()
        payload = {
            "runId": run_oid,
            "title": title,
            "company": str(safe.get("company") or "").strip(),
            "location": str(safe.get("location") or "").strip(),
            "boardName": str(
                safe.get("site") or safe.get("search_site") or "unknown"
            ).lower(),
            "externalId": str(safe.get("id") or ""),
            "jobDetails": {
                "description": str(safe.get("description") or ""),
                "requirements": [],
                "salary": {},
                "jobUrl": str(safe.get("job_url") or ""),
                "jobUrlDirect": str(safe.get("job_url_direct") or ""),
                "companyUrl": company_url,
                "searchKeyword": str(safe.get("search_keyword") or ""),
                "searchLocation": str(safe.get("search_location") or ""),
                "dedupeStrategy": dedupe_strategy,
                "dedupeKey": sha1(
                    str(dedupe_query).encode()
                ).hexdigest() if dedupe_strategy == "fingerprint" else None,
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

    print(
        f"[Jobs] total={total_scraped}, inserted={inserted}, "
        f"duplicates={duplicates}, accepted={accepted}, rejected={rejected}"
    )

    return {
        "total_scraped": total_scraped,
        "inserted": inserted,
        "duplicates": duplicates,
        "accepted": accepted,
        "rejected": rejected,
    }
