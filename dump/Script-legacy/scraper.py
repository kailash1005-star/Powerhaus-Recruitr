import os
import csv
import json
import logging
from firecrawl import Firecrawl
from pydantic import BaseModel
from typing import List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Pydantic schema for structured extraction ---

class JobListing(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    salary: Optional[str] = None
    location: Optional[str] = None
    skills: Optional[List[str]] = []
    url: Optional[str] = None

class JobListingsPage(BaseModel):
    jobs: List[JobListing]

class JobDescription(BaseModel):
    description: Optional[str] = None
    required_skills: Optional[List[str]] = []
    experience_required: Optional[str] = None
    role_summary: Optional[str] = None


def scrape_job_listings(firecrawl: Firecrawl, search_url: str) -> List[dict]:
    """Scrape job listings from Naukri search results page using structured JSON extraction."""
    logger.info(f"Scraping listings from: {search_url}")

    result = firecrawl.scrape(
        search_url,
        formats=[
            {
                "type": "json",
                "schema": JobListingsPage.model_json_schema(),
                "prompt": (
                    "Extract all job listings from this Naukri search results page. "
                    "For each job, extract: job title, company name, salary (or 'Not Disclosed'), "
                    "location, list of required skills/tags, and the full job URL link."
                )
            }
        ]
    )

    jobs = []
    if result and result.get("json"):
        raw = result["json"]
        for job in raw.get("jobs", []):
            jobs.append({
                "title": job.get("title"),
                "company": job.get("company"),
                "salary": job.get("salary", "Not Disclosed"),
                "location": job.get("location"),
                "skills": job.get("skills", []),
                "url": job.get("url"),
                "description": None
            })
    else:
        logger.warning("No structured JSON returned from listings page.")

    logger.info(f"Found {len(jobs)} job listings.")
    return jobs


def scrape_job_description(firecrawl: Firecrawl, job_url: str) -> dict:
    """Scrape and extract structured description from an individual job page."""
    logger.info(f"Scraping job description: {job_url}")
    try:
        result = firecrawl.scrape(
            job_url,
            formats=[
                {
                    "type": "json",
                    "schema": JobDescription.model_json_schema(),
                    "prompt": (
                        "Extract the job description, required skills list, "
                        "experience required, and a short role summary from this job posting."
                    )
                }
            ]
        )
        if result and result.get("json"):
            return result["json"]
    except Exception as e:
        logger.error(f"Error scraping job description from {job_url}: {e}")
    return {"description": "Not available", "required_skills": [], "experience_required": None, "role_summary": None}


def save_to_csv(jobs: List[dict], filename: str):
    """Save enriched job listings to a CSV file."""
    fieldnames = ["Title", "Company", "Salary", "Location", "Skills", "URL", "Description", "Experience", "Summary"]
    try:
        with open(filename, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for job in jobs:
                desc_data = job.get("description_data") or {}
                writer.writerow({
                    "Title": job.get("title", ""),
                    "Company": job.get("company", ""),
                    "Salary": job.get("salary", "Not Disclosed"),
                    "Location": job.get("location", ""),
                    "Skills": ", ".join(job.get("skills", [])),
                    "URL": job.get("url", ""),
                    "Description": desc_data.get("description", job.get("description", "")),
                    "Experience": desc_data.get("experience_required", ""),
                    "Summary": desc_data.get("role_summary", ""),
                })
        logger.info(f"Saved {len(jobs)} jobs to {filename}")
    except Exception as e:
        logger.error(f"Error saving CSV: {e}")


def run_scraper(
    search_url: str,
    output_file: str = "job_listings.csv",
    scrape_descriptions: bool = True
):
    import os
    # SECURITY: previously hardcoded key was leaked/burned — rotate at Firecrawl
    # and supply via env. Never restore a literal key here.
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY environment variable is not set.")

    firecrawl = Firecrawl(api_key=api_key)

    # Step 1: Scrape the search results page
    jobs = scrape_job_listings(firecrawl, search_url)

    # Step 2: Optionally enrich each listing with its full job description
    if scrape_descriptions:
        for i, job in enumerate(jobs):
            if job.get("url"):
                logger.info(f"[{i+1}/{len(jobs)}] Fetching description for: {job['title']} @ {job['company']}")
                job["description_data"] = scrape_job_description(firecrawl, job["url"])
            else:
                job["description_data"] = {}

    # Step 3: Save to CSV
    save_to_csv(jobs, output_file)
    return jobs


if __name__ == "__main__":
    SEARCH_URL = (
        "https://www.naukri.com/software-development-software-testing-python-java-software-engineer-jobs-in-chennai"
        "?k=software%20development%2C%20software%20testing%2C%20python%2C%20java%2C%20software%20engineer"
        "&l=chennai&experience=0&jobAge=1"
    )
    run_scraper(search_url=SEARCH_URL, output_file="job_listings.csv")
