"""
Runs API Endpoints
POST /api/v1/runs/start - Start a new recruitment run (background task)
GET  /api/v1/runs        - List all runs (paginated)
GET  /api/v1/runs/{id}   - Get run details
GET  /api/v1/runs/{id}/jobs - Get jobs for a run (paginated)
"""
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse
import json
import asyncio
from pydantic import BaseModel
from app.database import get_database
from app.security.tenant import TenantContext, tenant_scope
from app.schemas.runs import RunCreateSchema, RunResponseSchema
from app.services.orchestrator import process_run_background
from datetime import datetime, timedelta, timezone
from bson import ObjectId

router = APIRouter()


class RunRenameSchema(BaseModel):
    title: str


async def get_db():
    return await get_database()


async def owned_run(
    run_id: str,
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
) -> dict:
    """Guard for every ``/{run_id}/...`` route: load the run and refuse it unless
    the caller's tenant owns it (admins bypass). 404 for both 'missing' and 'not
    yours' so ids can't be probed across tenants."""
    try:
        oid = ObjectId(run_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Run not found")
    doc = await db["runs"].find_one({"_id": oid})
    if not ctx.owns(doc):
        raise HTTPException(status_code=404, detail="Run not found")
    return doc


# ── POST /start ─────────────────────────────────────────────────────────

@router.post("/start", response_model=RunResponseSchema)
async def start_run(
    request: RunCreateSchema,
    background_tasks: BackgroundTasks,
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    try:
        if not request.runConfig.targetIndustries:
            raise HTTPException(status_code=400, detail="targetIndustries must contain at least one industry")
        runs_col = db["runs"]
        run_doc = {
            "title": request.title,
            "source": request.source,
            "runStartedAt": datetime.utcnow(),
            "status": "active",
            "currentPhase": "pending",
            "tenantId": ctx.tenant_id,
            "createdBy": ctx.sub,
            "createdByEmail": ctx.email,
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
            tenant_id=ctx.tenant_id,
        )

        run_doc["_id"] = run_id
        return RunResponseSchema(**run_doc)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error starting run: {str(e)}")


# ── GET / (list, paginated) ─────────────────────────────────────────────

@router.get("", response_model=list[RunResponseSchema])
async def list_runs(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    try:
        runs_col = db["runs"]
        skip = (page - 1) * limit
        cursor = runs_col.find(ctx.read_filter()).sort("createdAt", -1).skip(skip).limit(limit)
        runs = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            runs.append(RunResponseSchema(**doc))
        return runs
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing runs: {str(e)}")


# ── GET /{run_id} ────────────────────────────────────────────────────────

@router.get("/{run_id}", response_model=RunResponseSchema)
async def get_run(run_id: str, run: dict = Depends(owned_run)):
    run["_id"] = str(run["_id"])
    return RunResponseSchema(**run)


# ── GET /{run_id}/jobs ───────────────────────────────────────────────────

@router.get("/{run_id}/jobs")
async def get_run_jobs(
    run_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort_by: str = Query("createdAt", description="Sort field: title|company|location|boardName|qualityStatus|createdAt"),
    sort_order: str = Query("desc", description="Sort order: asc|desc"),
    _run: dict = Depends(owned_run),
    db=Depends(get_db),
):
    """Return paginated jobs for a run with summary counts.

    The run results screen only deals with ACCEPTED jobs, so this endpoint
    always scopes to ``qualityStatus == "good"`` — rejected ("poor") jobs are
    never returned here.
    """
    try:
        jobs_col = db["jobs"]
        run_oid = ObjectId(run_id)

        query: dict = {"runId": run_oid, "qualityStatus": "good"}

        total = await jobs_col.count_documents(query)
        skip = (page - 1) * limit
        
        # Validate sort_order
        sort_direction = 1 if sort_order == "asc" else -1
        
        cursor = jobs_col.find(query).sort(sort_by, sort_direction).skip(skip).limit(limit)

        prospects_col = db["prospects"]
        pipelines_col = db["candidatePipelines"]

        jobs = []
        company_ids_seen: dict = {}
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            doc["runId"] = str(doc["runId"])
            company_oid = doc.get("companyId")
            if company_oid:
                doc["companyId"] = str(company_oid)
                # Count prospects per company (memoize)
                if company_oid not in company_ids_seen:
                    company_ids_seen[company_oid] = await prospects_col.count_documents(
                        {"companyId": company_oid, "isAccepted": True}
                    )
                doc["prospectCount"] = company_ids_seen[company_oid]
            else:
                doc["prospectCount"] = 0
            doc["industry"] = doc.get("industry") or ""
            doc["outreachCount"] = 0
            # Is this job already in any candidate pipeline? Drives the
            # "Add to pipeline" button enable/disable in the UI.
            pipe = await pipelines_col.find_one(
                {"jobs.jobId": doc["_id"]},
                {"_id": 1, "companyName": 1},
            )
            if pipe:
                doc["inPipeline"] = True
                doc["inPipelineId"] = str(pipe["_id"])
                doc["inPipelineCompany"] = pipe.get("companyName")
            else:
                doc["inPipeline"] = False
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


# ── PATCH /{run_id} (rename) ──────────────────────────────────────────────

@router.patch("/{run_id}", response_model=RunResponseSchema)
async def rename_run(run_id: str, body: RunRenameSchema, _run: dict = Depends(owned_run), db=Depends(get_db)):
    """Rename a run (update its title)."""
    try:
        title = (body.title or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="title must not be empty")

        runs_col = db["runs"]
        run_oid = ObjectId(run_id)
        res = await runs_col.update_one(
            {"_id": run_oid},
            {"$set": {"title": title, "updatedAt": datetime.utcnow()}},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Run not found")

        doc = await runs_col.find_one({"_id": run_oid})
        doc["_id"] = str(doc["_id"])
        return RunResponseSchema(**doc)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error renaming run: {str(e)}")


# ── DELETE /{run_id} ─────────────────────────────────────────────────────

@router.delete("/{run_id}")
async def delete_run(run_id: str, _run: dict = Depends(owned_run), db=Depends(get_db)):
    """Delete a run and ALL data associated with it: jobs, prospects, outreach,
    and any companies that become orphaned (no other run references them).

    Companies are shared/deduped across runs (keyed by domain/slug), so we only
    remove a company when no remaining job points to it — otherwise we'd corrupt
    other runs that reuse the same company.
    """
    try:
        run_oid = ObjectId(run_id)
        runs_col = db["runs"]
        jobs_col = db["jobs"]
        prospects_col = db["prospects"]
        companies_col = db["companies"]
        outreach_col = db["outreach"]

        # Capture the companies this run touched BEFORE deleting its jobs.
        company_ids = [
            cid for cid in await jobs_col.distinct("companyId", {"runId": run_oid}) if cid
        ]

        # Delete run-scoped data. outreach may not exist yet → delete_many is a no-op.
        prospects_res = await prospects_col.delete_many({"runId": run_oid})
        outreach_res = await outreach_col.delete_many({"runId": run_oid})
        jobs_res = await jobs_col.delete_many({"runId": run_oid})

        # Remove companies that are now orphaned (no remaining job references them).
        deleted_companies = 0
        for cid in company_ids:
            if await jobs_col.count_documents({"companyId": cid}) == 0:
                res = await companies_col.delete_one({"_id": cid})
                deleted_companies += res.deleted_count

        runs_res = await runs_col.delete_one({"_id": run_oid})
        if runs_res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Run not found")

        return {
            "success": True,
            "message": "Run and associated data deleted successfully",
            "deleted": {
                "jobs": jobs_res.deleted_count,
                "prospects": prospects_res.deleted_count,
                "outreach": outreach_res.deleted_count,
                "companies": deleted_companies,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting run: {str(e)}")


# ── Stub endpoints used by the new run-results UI ───────────────────────

@router.get("/{run_id}/enrichment-credits")
async def get_enrichment_credits(run_id: str, _run: dict = Depends(owned_run)):
    """Stub credit status — enrichment is out of scope this iteration."""
    period_end = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return {
        "creditsUsed": 0,
        "dailyLimit": 100,
        "creditsRemaining": 100,
        "perJobLimit": 50,
        "jobCredits": {},
        "periodEnd": period_end.isoformat(),
    }


@router.get("/{run_id}/outreach-status")
async def get_outreach_status(run_id: str, _run: dict = Depends(owned_run)):
    return {"records": []}


@router.post("/{run_id}/trigger-email-flow")
async def trigger_email_flow(run_id: str, _run: dict = Depends(owned_run)):
    return {"message": "Email outreach is not enabled in this iteration"}


# ── SSE stream for real-time pipeline progress ──────────────────────────

@router.get("/{run_id}/stream")
async def stream_run_progress(run_id: str, _run: dict = Depends(owned_run), db=Depends(get_db)):
    """SSE endpoint that streams pipeline phase transitions in real time.
    
    The frontend connects after POST /start returns, and receives events
    as the orchestrator updates `currentPhase` on the run document.
    Events: phase (with phase name + stats), done, error.
    """
    run_oid = ObjectId(run_id)

    async def event_generator():
        last_phase = None
        last_stats = None
        polls = 0
        max_polls = 600  # 10 minutes max (600 * 1s)

        while polls < max_polls:
            doc = await db["runs"].find_one(
                {"_id": run_oid},
                {"status": 1, "currentPhase": 1, "stats": 1, "error": 1},
            )
            if not doc:
                yield _sse("error", {"message": "Run not found"})
                return

            phase = doc.get("currentPhase") or "pending"
            status = doc.get("status") or "active"
            stats = doc.get("stats") or {}

            # Emit an event whenever phase or stats change
            if phase != last_phase or stats != last_stats:
                last_phase = phase
                last_stats = dict(stats)  # shallow copy

                if status == "completed" or phase == "done":
                    yield _sse("phase", {"phase": "done", "stats": stats})
                    yield _sse("done", {"runId": run_id})
                    return

                if status in ("cancelled", "failed") or phase == "failed":
                    yield _sse("error", {
                        "message": doc.get("error") or "Run failed",
                        "phase": phase,
                    })
                    return

                yield _sse("phase", {"phase": phase, "stats": stats})

            await asyncio.sleep(1)
            polls += 1

        yield _sse("error", {"message": "Stream timed out"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
