"""
Matching API — CV ↔ JD engine.

  POST /api/v1/matching/cv/upload     — CV dump: upload 1..N files (background ingest)
  GET  /api/v1/matching/cv/batch/{id} — ingestion progress for a batch
  GET  /api/v1/matching/cv            — list ingested CVs (paginated)
  GET  /api/v1/matching/cv/{id}       — inspect one parsed CV
  POST /api/v1/matching/run           — THE "Run Matching" button → top-N + reasons
  GET  /api/v1/matching/run/{id}      — fetch a prior match result
"""
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.config import settings
from app.database import get_database
from app.security.tenant import TenantContext, tenant_scope
from app.services import matching_service
from app.services import email_service
from app.services import llm_extraction_service as llm_extraction
from app.startup_checks import matching_readiness

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_db():
    return await get_database()


def _require_matching_ready():
    """Fail fast with a clear message if the engine can't run (e.g. backend
    started with the wrong Python and parser deps are missing) — instead of letting
    every CV silently fail to parse."""
    status = matching_readiness()
    if not status["ready"]:
        raise HTTPException(
            status_code=503,
            detail=(
                "Matching engine not ready — missing: "
                f"{', '.join(status['missing'])}. The backend is likely running "
                "the wrong Python. Restart it with the venv: "
                ".\\venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000"
            ),
        )


def _oid(id_str: str):
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        return ObjectId(id_str)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="invalid id")


# ── CV ingestion ─────────────────────────────────────────────────────────────
async def _ingest_batch(db, files: List[tuple], batch_id: str, tenant_id: Optional[str] = None):
    """Background worker: ingest each (filename, bytes) with bounded concurrency."""
    import asyncio
    sem = asyncio.Semaphore(4)  # cap concurrent parse/LLM work

    async def one(filename, data):
        async with sem:
            await matching_service.ingest_cv(db, data, filename, batch_id, tenant_id=tenant_id)

    await asyncio.gather(*[one(fn, data) for fn, data in files])
    logger.info("[Matching] batch %s ingestion complete (%d files)", batch_id, len(files))


@router.post("/cv/upload")
async def upload_cvs(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    """Upload a CV dump. Files are validated, read, then ingested in the
    background. Returns a batchId to poll for progress."""
    _require_matching_ready()
    if not files:
        raise HTTPException(status_code=400, detail="no files provided")

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    payload: List[tuple] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail=f"{f.filename} exceeds {settings.MAX_UPLOAD_MB}MB")
        payload.append((f.filename, data))

    if not payload:
        raise HTTPException(status_code=400, detail="all files were empty")

    batch_id = uuid.uuid4().hex
    background_tasks.add_task(_ingest_batch, db, payload, batch_id, ctx.tenant_id)
    return {"batchId": batch_id, "received": len(payload), "status": "processing"}


@router.get("/cv/stats")
async def cv_stats(ctx: TenantContext = Depends(tenant_scope), db=Depends(get_db)):
    """Corpus health at a glance: how many CVs are matchable vs dead.

    Exists because 22 of 44 CVs sat in status=failed for days and every match
    run looked normal — the UI needs one cheap number to make a silent half-dead
    corpus impossible.
    """
    cursor = db["cv_candidates"].aggregate([
        {"$match": ctx.read_filter()},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ])
    counts = {row["_id"]: row["count"] async for row in cursor}
    return {"total": sum(counts.values()), "counts": counts}


@router.post("/cv/reprocess-failed")
async def reprocess_failed_cvs(background_tasks: BackgroundTasks, ctx: TenantContext = Depends(tenant_scope), db=Depends(get_db)):
    """Re-ingest every failed CV from its stored original bytes.

    Why this exists: 22 of 44 production CVs failed under the OLD parser
    (Docling crashed on their first page) and stayed dead after it was replaced
    — the only retry path was manually re-uploading each file, so half the
    corpus was silently unmatchable. Every failed doc still has its original
    bytes in `cv_files`; this re-runs them through the CURRENT parser.
    """
    _require_matching_ready()
    payload: List[tuple] = []
    unrecoverable: List[str] = []
    async for doc in db["cv_candidates"].find(ctx.read_filter({"status": "failed"}), {"sourceFileName": 1}):
        f = await db["cv_files"].find_one({"_id": doc["_id"]})
        if f and f.get("data"):
            payload.append((f.get("filename") or doc.get("sourceFileName"), bytes(f["data"])))
        else:
            unrecoverable.append(doc.get("sourceFileName") or str(doc["_id"]))

    if not payload:
        return {"queued": 0, "unrecoverable": unrecoverable, "batchId": None,
                "message": "No failed CVs with stored originals to reprocess."}

    batch_id = uuid.uuid4().hex
    background_tasks.add_task(_ingest_batch, db, payload, batch_id, ctx.tenant_id)
    return {"queued": len(payload), "unrecoverable": unrecoverable,
            "batchId": batch_id, "status": "processing"}


@router.get("/cv/batch/{batch_id}")
async def batch_status(batch_id: str, ctx: TenantContext = Depends(tenant_scope), db=Depends(get_db)):
    col = db["cv_candidates"]
    cursor = col.aggregate([
        {"$match": ctx.read_filter({"batchId": batch_id})},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ])
    counts = {row["_id"]: row["count"] async for row in cursor}
    total = sum(counts.values())
    done = counts.get("embedded", 0) + counts.get("failed", 0)
    return {
        "batchId": batch_id,
        "total": total,
        "counts": counts,
        "complete": total > 0 and done >= total,
    }


@router.get("/cv")
async def list_cvs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    col = db["cv_candidates"]
    skip = (page - 1) * limit
    scope = ctx.read_filter()
    total = await col.count_documents(scope)
    items = []
    cursor = col.find(scope, {"markdown": 0, "embedding.vector": 0}).sort("createdAt", -1).skip(skip).limit(limit)
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        items.append(doc)
    return {"total": total, "page": page, "limit": limit, "items": items}


@router.get("/cv/{cv_id}")
async def get_cv(cv_id: str, ctx: TenantContext = Depends(tenant_scope), db=Depends(get_db)):
    col = db["cv_candidates"]
    doc = await col.find_one({"_id": _oid(cv_id)}, {"embedding.vector": 0})
    if not doc or not ctx.owns(doc):
        raise HTTPException(status_code=404, detail="CV not found")
    doc["_id"] = str(doc["_id"])
    return doc


@router.get("/cv/{cv_id}/download")
async def download_cv(cv_id: str, ctx: TenantContext = Depends(tenant_scope), db=Depends(get_db)):
    """Download a candidate's CV. Serves the ORIGINAL uploaded file when we still
    have its bytes; otherwise falls back to the parsed text as a .txt so the
    button always returns something usable."""
    oid = _oid(cv_id)
    # Authorize against the CV record's tenant before serving any bytes.
    cv_meta = await db["cv_candidates"].find_one({"_id": oid}, {"tenantId": 1})
    if not cv_meta or not ctx.owns(cv_meta):
        raise HTTPException(status_code=404, detail="CV not found")
    f = await db["cv_files"].find_one({"_id": oid})
    if f and f.get("data"):
        filename = f.get("filename") or f"cv-{cv_id}"
        return Response(
            content=bytes(f["data"]),
            media_type=f.get("contentType") or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    cv = await db["cv_candidates"].find_one({"_id": oid}, {"markdown": 1, "profile": 1, "sourceFileName": 1})
    if not cv:
        raise HTTPException(status_code=404, detail="CV not found")
    text = cv.get("markdown") or "(no parsed text available for this CV)"
    base = (cv.get("profile") or {}).get("fullName") or cv.get("sourceFileName") or f"cv-{cv_id}"
    safe = "".join(c for c in str(base) if c.isalnum() or c in " -_").strip() or f"cv-{cv_id}"
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe}.txt"'},
    )


# ── The match (button) ───────────────────────────────────────────────────────
class MatchTextRequest(BaseModel):
    jdText: str
    returnTop: Optional[int] = None


@router.post("/run")
async def run_matching(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    jdText: Optional[str] = Form(None),
    returnTop: Optional[int] = Form(None),
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    """THE 'Run Matching' button. Accepts a JD as an uploaded document (multipart
    `file`) OR raw text (`jdText`). Runs the full pipeline and returns top-N
    candidates with reasoning + contact."""
    _require_matching_ready()
    try:
        if file is not None:
            data = await file.read()
            if not data:
                raise HTTPException(status_code=400, detail="empty JD file")
            max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
            if len(data) > max_bytes:
                raise HTTPException(status_code=413, detail=f"JD exceeds {settings.MAX_UPLOAD_MB}MB")
            result = await matching_service.run_match(
                db, jd_bytes=data, jd_filename=file.filename, return_top=returnTop,
                tenant_id=ctx.tenant_id,
            )
        elif jdText and jdText.strip():
            result = await matching_service.run_match(
                db, jd_text=jdText, return_top=returnTop, tenant_id=ctx.tenant_id)
        else:
            raise HTTPException(status_code=400, detail="provide a JD file or jdText")
        return result
    except HTTPException:
        raise
    except llm_extraction.ExtractionError as e:
        # The JD parse failed after retries. This is deliberately a hard error:
        # the old behavior silently scored on similarity alone and looked fine.
        logger.exception("[Matching] JD extraction failed")
        raise HTTPException(status_code=502, detail=f"JD parsing failed — run not scored: {e}")
    except Exception as e:  # noqa: BLE001
        logger.exception("[Matching] run failed")
        raise HTTPException(status_code=500, detail=f"matching failed: {e}")


@router.post("/run/json")
async def run_matching_json(req: MatchTextRequest, ctx: TenantContext = Depends(tenant_scope), db=Depends(get_db)):
    """JSON convenience variant of /run for raw-text JDs."""
    _require_matching_ready()
    try:
        return await matching_service.run_match(
            db, jd_text=req.jdText, return_top=req.returnTop, tenant_id=ctx.tenant_id)
    except llm_extraction.ExtractionError as e:
        logger.exception("[Matching] JD extraction failed")
        raise HTTPException(status_code=502, detail=f"JD parsing failed — run not scored: {e}")
    except Exception as e:  # noqa: BLE001
        logger.exception("[Matching] run(json) failed")
        raise HTTPException(status_code=500, detail=f"matching failed: {e}")


@router.get("/runs")
async def list_match_runs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    """Past matching runs — one per saved JD, with its top candidates. Powers the
    'Past runs' history in Candidate Matching."""
    col = db["match_runs"]
    skip = (page - 1) * limit
    scope = ctx.read_filter()
    total = await col.count_documents(scope)
    items = []
    # The full per-candidate analysis is large and the history list only renders
    # headlines — exclude it here; GET /run/{id} serves it.
    cursor = col.find(scope, {"analysis": 0, "logs": 0}).sort("createdAt", -1).skip(skip).limit(limit)
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        items.append(doc)
    return {"total": total, "page": page, "limit": limit, "items": items}


@router.get("/run/{match_run_id}")
async def get_match_run(
    match_run_id: str,
    analysis: bool = Query(True, description="Include the full per-candidate scoring analysis"),
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_db),
):
    """A saved run. Carries `analysis.candidates` — EVERY candidate scored, ranked,
    each with the weighted breakdown behind its number — not just the top-N in
    `results`. Pass `analysis=false` while polling a running job to keep the
    payload small."""
    col = db["match_runs"]
    projection = None if analysis else {"analysis": 0}
    doc = await col.find_one({"_id": _oid(match_run_id)}, projection)
    if not doc or not ctx.owns(doc):
        raise HTTPException(status_code=404, detail="match run not found")
    doc["_id"] = str(doc["_id"])
    return doc


# ── Outreach email ───────────────────────────────────────────────────────────
class OutreachDraftRequest(BaseModel):
    candidateId: str
    roleTitle: Optional[str] = None


class OutreachSendRequest(BaseModel):
    to: str
    subject: str
    body: str
    candidateId: Optional[str] = None


@router.get("/outreach/config")
async def outreach_config():
    """Whether actual sending is enabled (SMTP configured). Drafting always works."""
    return {"sendEnabled": email_service.email_configured()}


@router.post("/outreach/draft")
async def outreach_draft(req: OutreachDraftRequest, ctx: TenantContext = Depends(tenant_scope), db=Depends(get_db)):
    """Generate a professional HR-toned outreach email for a candidate.

    Looks the candidate up in the CV corpus first; falls back to a pipeline
    candidate (Apify-enriched) so "Reach out" works for pipeline match results.
    """
    oid = _oid(req.candidateId)
    cv = await db["cv_candidates"].find_one({"_id": oid}, {"profile": 1, "contact": 1, "tenantId": 1})
    if cv and ctx.owns(cv):
        profile = cv.get("profile") or {}
        contact = cv.get("contact") or {}
    else:
        cand = await db["candidates"].find_one({"_id": oid})
        if not cand or not ctx.owns(cand):
            raise HTTPException(status_code=404, detail="candidate not found")
        enrichment = cand.get("apifyEnrichment") or {}
        profile = enrichment.get("profile") or {
            "fullName": cand.get("displayName"),
            "currentTitle": cand.get("currentTitle"),
        }
        contact = enrichment.get("contact") or {}
        # Apollo verified email lives on enrichedData when the Apollo stage ran.
        if not contact.get("email"):
            contact = {**contact, "email": (cand.get("enrichedData") or {}).get("email")}
    try:
        from app.services import cost_service
        async with cost_service.cost_context(
            cost_service.STAGE_OUTREACH, label=profile.get("fullName"),
            candidateId=req.candidateId,
        ):
            draft = await email_service.generate_outreach_email(profile, req.roleTitle)
    except Exception as e:  # noqa: BLE001
        logger.exception("[Outreach] draft failed")
        raise HTTPException(status_code=500, detail=f"draft failed: {e}")
    return {
        "to": contact.get("email"),
        "subject": draft["subject"],
        "body": draft["body"],
        "sendEnabled": email_service.email_configured(),
    }


@router.post("/outreach/send")
async def outreach_send(req: OutreachSendRequest):
    """Send the (possibly edited) email. 503 with a clear message if SMTP isn't set up."""
    try:
        return await email_service.send_email(req.to, req.subject, req.body)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("[Outreach] send failed")
        raise HTTPException(status_code=500, detail=f"send failed: {e}")
