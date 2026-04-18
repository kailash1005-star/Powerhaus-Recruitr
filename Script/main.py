import os
import json
import logging
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from typing import Optional
from scraper import run_scraper

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Naukri Job Scraper", version="1.0.0")

# In-memory job store (for Cloud Run single-instance use)
# For production multi-instance, swap this for Cloud Firestore or GCS
scrape_results: dict = {}


class ScrapeRequest(BaseModel):
    search_url: Optional[str] = (
        "https://www.naukri.com/software-development-software-testing-python-java-software-engineer-jobs-in-chennai"
        "?k=software%20development%2C%20software%20testing%2C%20python%2C%20java%2C%20software%20engineer"
        "&l=chennai&experience=0&jobAge=1"
    )
    scrape_descriptions: Optional[bool] = True
    output_file: Optional[str] = "job_listings.csv"


def _run_job(job_id: str, request: ScrapeRequest):
    """Background task to run the scraper."""
    scrape_results[job_id] = {"status": "running", "jobs": []}
    try:
        jobs = run_scraper(
            search_url=request.search_url,
            output_file=f"/tmp/{request.output_file}",
            scrape_descriptions=request.scrape_descriptions
        )
        scrape_results[job_id] = {"status": "completed", "count": len(jobs), "jobs": jobs}
        logger.info(f"Job {job_id} completed with {len(jobs)} results.")
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        scrape_results[job_id] = {"status": "failed", "error": str(e)}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/scrape")
async def start_scrape(request: ScrapeRequest, background_tasks: BackgroundTasks):
    """Kick off a scrape job in the background and return a job ID."""
    import uuid
    job_id = str(uuid.uuid4())
    background_tasks.add_task(_run_job, job_id, request)
    return {"job_id": job_id, "status": "started"}


@app.get("/scrape/{job_id}/status")
def job_status(job_id: str):
    """Poll for the status of a scrape job."""
    result = scrape_results.get(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **result}


@app.get("/scrape/{job_id}/download")
def download_csv(job_id: str, filename: str = Query(default="job_listings.csv")):
    """Download the CSV output for a completed scrape job."""
    result = scrape_results.get(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    if result["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Job is not complete. Current status: {result['status']}")
    filepath = f"/tmp/{filename}"
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="CSV file not found")
    return FileResponse(filepath, media_type="text/csv", filename=filename)


@app.get("/scrape/sync")
def scrape_sync(
    url: Optional[str] = Query(
        default=(
            "https://www.naukri.com/software-development-software-testing-python-java-software-engineer-jobs-in-chennai"
            "?k=software%20development%2C%20software%20testing%2C%20python%2C%20java%2C%20software%20engineer"
            "&l=chennai&experience=0&jobAge=1"
        )
    ),
    descriptions: bool = Query(default=False)
):
    """Synchronous scrape (no descriptions) — fast, suitable for quick checks."""
    try:
        jobs = run_scraper(
            search_url=url,
            output_file="/tmp/sync_job_listings.csv",
            scrape_descriptions=descriptions
        )
        return {"count": len(jobs), "jobs": jobs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
