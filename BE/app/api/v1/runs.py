"""
Runs API Endpoints
POST /api/v1/runs/start - Start a new recruitment run (background task)
GET  /api/v1/runs        - List all runs (paginated)
GET  /api/v1/runs/{id}   - Get run details
GET  /api/v1/runs/{id}/jobs - Get jobs for a run (paginated)
"""
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Query
from app.database import get_database
from app.schemas.runs import RunCreateSchema, RunResponseSchema
from app.services.orchestrator import process_run_background
from datetime import datetime
from bson import ObjectId

router = APIRouter()


async def get_db():
    return await get_database()


# ── POST /start ─────────────────────────────────────────────────────────

@router.post("/start", response_model=RunResponseSchema)
async def start_run(
    request: RunCreateSchema,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    try:
        runs_col = db["runs"]
        run_doc = {
            "title": request.title,
            "source": request.source,
            "runStartedAt": datetime.utcnow(),
            "status": "active",
            "stats": {
                "totalJobsScraped": 0,
                "uniqueCompanies": 0,
                "acceptedCompanies": 0,
                "rejectedCompanies": 0,
                "totalProspects": 0,
            },
            "runConfig": request.runConfig.model_dump(),
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }
        result = await runs_col.insert_one(run_doc)
        run_id = str(result.inserted_id)

        background_tasks.add_task(
            process_run_background,
            run_id=run_id,
            run_config=request.runConfig.model_dump(),
        )

        run_doc["_id"] = run_id
        return RunResponseSchema(**run_doc)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting run: {str(e)}")


# ── GET / (list, paginated) ─────────────────────────────────────────────

@router.get("", response_model=list[RunResponseSchema])
async def list_runs(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    db=Depends(get_db),
):
    try:
        runs_col = db["runs"]
        skip = (page - 1) * limit
        cursor = runs_col.find().sort("createdAt", -1).skip(skip).limit(limit)
        runs = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            runs.append(RunResponseSchema(**doc))
        return runs
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing runs: {str(e)}")


# ── GET /{run_id} ────────────────────────────────────────────────────────

@router.get("/{run_id}", response_model=RunResponseSchema)
async def get_run(run_id: str, db=Depends(get_db)):
    try:
        runs_col = db["runs"]
        doc = await runs_col.find_one({"_id": ObjectId(run_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Run not found")
        doc["_id"] = str(doc["_id"])
        return RunResponseSchema(**doc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching run: {str(e)}")


# ── GET /{run_id}/jobs ───────────────────────────────────────────────────

@router.get("/{run_id}/jobs")
async def get_run_jobs(
    run_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    quality: str = Query(None, description="Filter by qualityStatus: good|poor"),
    sort_by: str = Query("createdAt", description="Sort field: title|company|location|boardName|qualityStatus|createdAt"),
    sort_order: str = Query("desc", description="Sort order: asc|desc"),
    db=Depends(get_db),
):
    """Return paginated jobs for a run with summary counts."""
    try:
        jobs_col = db["jobs"]
        run_oid = ObjectId(run_id)

        query: dict = {"runId": run_oid}
        if quality:
            query["qualityStatus"] = quality

        total = await jobs_col.count_documents(query)
        skip = (page - 1) * limit
        
        # Validate sort_order
        sort_direction = 1 if sort_order == "asc" else -1
        
        cursor = jobs_col.find(query).sort(sort_by, sort_direction).skip(skip).limit(limit)

        jobs = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            doc["runId"] = str(doc["runId"])
            if doc.get("companyId"):
                doc["companyId"] = str(doc["companyId"])
            jobs.append(doc)

        return {
            "total": total,
            "page": page,
            "limit": limit,
            "pages": max(1, (total + limit - 1) // limit),
            "jobs": jobs,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching jobs: {str(e)}")
