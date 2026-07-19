"""Startup reaper for orphaned background runs.

Every long-running flow here (discovery, enrichment, match runs) executes as an
in-process asyncio task. A container restart, deploy, or scale-in kills those
tasks silently, leaving their status documents stuck on "running"/"queued"
forever — the UI polls a run that no process is executing.

This reaper runs once at boot: anything still marked running whose last
heartbeat (updatedAt) is older than STALE_RUN_REAP_MINUTES is flipped to
"failed" with an explicit orphan message, so the recruiter sees a retryable
failure instead of an eternal spinner.

The age threshold — rather than reaping every running run at boot — is what
makes this safe under multiple Uvicorn workers: each worker boots and runs the
sweep, and a run that IS alive in a sibling worker keeps refreshing updatedAt,
so it never ages past the cutoff.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.config import settings
from app.database import get_database

logger = logging.getLogger(__name__)

_ORPHAN_MSG = ("Orphaned: the server restarted while this run was in flight. "
               "Re-run to continue.")


async def reap_stale_runs() -> None:
    """Fail-forward every run orphaned by a restart. Never raises — a reaper
    that blocks boot is worse than the stuck runs it cleans up."""
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=settings.STALE_RUN_REAP_MINUTES)
        db = await get_database()

        res = await db["match_runs"].update_many(
            {"status": "running", "updatedAt": {"$lt": cutoff}},
            {"$set": {"status": "failed", "error": _ORPHAN_MSG,
                      "updatedAt": datetime.utcnow()}},
        )
        if res.modified_count:
            logger.warning("[Reaper] failed %d orphaned match run(s)", res.modified_count)

        # Pipeline jobs: search + enrich statuses live on the embedded jobs[]
        # array. The pipeline's own updatedAt is refreshed by every attempt/flush
        # a live worker makes, so an old updatedAt means nobody is working on it.
        pipelines = db["candidatePipelines"]
        stale = {"updatedAt": {"$lt": cutoff}}
        res = await pipelines.update_many(
            {**stale, "jobs.searchStatus": {"$in": ["running", "queued"]}},
            {"$set": {"jobs.$[j].searchStatus": "failed",
                      "jobs.$[j].searchError": _ORPHAN_MSG}},
            array_filters=[{"j.searchStatus": {"$in": ["running", "queued"]}}],
        )
        if res.modified_count:
            logger.warning("[Reaper] failed orphaned searches on %d pipeline(s)", res.modified_count)

        res = await pipelines.update_many(
            {**stale, "jobs.enrichStatus": {"$in": ["running", "queued"]}},
            {"$set": {"jobs.$[j].enrichStatus": "failed",
                      "jobs.$[j].enrichError": _ORPHAN_MSG}},
            array_filters=[{"j.enrichStatus": {"$in": ["running", "queued"]}}],
        )
        if res.modified_count:
            logger.warning("[Reaper] failed orphaned enrichments on %d pipeline(s)", res.modified_count)

        # CV ingest batches stuck mid-parse.
        res = await db["cv_candidates"].update_many(
            {"status": {"$in": ["pending", "processing"]}, "updatedAt": {"$lt": cutoff}},
            {"$set": {"status": "failed", "error": _ORPHAN_MSG,
                      "updatedAt": datetime.utcnow()}},
        )
        if res.modified_count:
            logger.warning("[Reaper] failed %d orphaned CV ingest(s)", res.modified_count)
    except Exception as exc:  # noqa: BLE001 — never block boot
        logger.warning("[Reaper] sweep skipped: %s", exc)
