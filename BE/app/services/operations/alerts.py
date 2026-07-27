"""Operator alerts — tell the person who can fix it, once.

What this is for
----------------
Some failures are the operator's problem, not the user's: an expired API token,
an exhausted plan. The pipeline cannot recover from them, and today nobody finds
out until someone notices the output is empty. This module is the one path from
"a background job hit an unrecoverable vendor wall" to "the operator's inbox".

Three properties, in order of importance
----------------------------------------
1. **It cannot break what it watches.** Every send is fire-and-forget and the
   whole body is wrapped. SMTP is a blocking network call with a 20s timeout
   (``email_service._send_sync``); an alerter that stalls or crashes the pipeline
   it monitors is strictly worse than no alerter. Note ``send_email`` RAISES when
   unconfigured, so it is never called bare.

2. **It deduplicates.** This is what makes email viable at all. A dead token
   fails on every job of every run — without a cooldown that is fifty identical
   emails in two minutes, which teaches the operator to ignore the channel, and
   an ignored channel is worse than none. One email per cause per cooldown
   window; the suppressed count rides along on the next one so throttling never
   hides scale.

3. **It says what to DO.** "Apify failed" is not actionable — a dead token and an
   exhausted plan look identical and need opposite responses (rotate the key vs.
   upgrade the account, where rotating does nothing). The caller supplies the
   remedy; classification lives in ``sourcing.apify_health``.

Multi-worker safety
-------------------
Cloud Run runs ``UVICORN_WORKERS`` processes, each with its own memory, so an
in-process cooldown would send one email per worker. The claim is therefore a
single conditional Mongo update: exactly one worker's update matches, the rest
fall through to incrementing the suppressed counter. Same reasoning as the
reaper's age cutoff (``operations/run_reaper.py``) — coordinate through the
database, don't try to hold a lock.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

COLLECTION = "operator_alerts"


def alert_recipients() -> List[str]:
    """Who gets told. Falls back to ADMIN_EMAILS so that configuring alerts does
    not mean maintaining a second copy of the operator list."""
    raw = settings.ALERT_RECIPIENTS or settings.ADMIN_EMAILS or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def alerts_configured() -> bool:
    """True when an alert could actually be delivered right now."""
    try:
        from app.services.operations import email_service

        return bool(
            settings.ALERTS_ENABLED
            and email_service.email_configured()
            and alert_recipients()
        )
    except Exception:  # noqa: BLE001
        return False


async def _claim_send(dedup_key: str, cooldown_minutes: int) -> Optional[int]:
    """Try to win the right to send for this cause.

    Returns the number of occurrences suppressed since the last send if this
    caller won, or None if another worker sent recently (or the claim failed).

    The whole decision is ONE atomic update so concurrent workers cannot both
    win: only the document whose ``lastSentAt`` is older than the cooldown
    matches the filter, and the update moves it forward in the same operation.
    """
    from app.database import get_collection

    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=max(1, cooldown_minutes))
    col = await get_collection(COLLECTION)

    # Fast path: an existing, cooled-down alert. Returns the pre-update doc, so
    # suppressedCount is the tally accumulated since the previous send.
    won = await col.find_one_and_update(
        {"_id": dedup_key, "lastSentAt": {"$lte": cutoff}},
        {"$set": {"lastSentAt": now, "suppressedCount": 0}},
    )
    if won is not None:
        return int(won.get("suppressedCount") or 0)

    # No match: either the key is new (send, and record it) or it is still
    # cooling down (count the occurrence and stay quiet). upsert distinguishes
    # them without a second round trip — on a brand-new key `upserted_id` is set.
    result = await col.update_one(
        {"_id": dedup_key},
        {
            "$inc": {"suppressedCount": 1},
            "$setOnInsert": {"firstSeenAt": now, "lastSentAt": now},
        },
        upsert=True,
    )
    if result.upserted_id is not None:
        # First time we have ever seen this cause — send immediately.
        await col.update_one({"_id": dedup_key}, {"$set": {"suppressedCount": 0}})
        return 0
    return None


def _compose(title: str, detail: str, action: str, suppressed: int,
             extra: Optional[Dict[str, Any]]) -> str:
    lines = [
        detail.strip(),
        "",
        "WHAT TO DO",
        action.strip(),
        "",
    ]
    if extra:
        lines.append("DETAILS")
        for k, v in extra.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
    if suppressed:
        lines.append(
            f"NOTE: this happened {suppressed + 1} times since the last email; "
            f"further occurrences within the cooldown are counted, not sent."
        )
        lines.append("")
    lines.append(f"-- Recruitr operator alert, {datetime.utcnow():%Y-%m-%d %H:%M} UTC")
    return "\n".join(lines)


async def _deliver(title: str, body: str) -> None:
    from app.services.operations import email_service

    for recipient in alert_recipients():
        try:
            await email_service.send_email(recipient, f"[Recruitr] {title}", body)
            logger.info("[Alert] sent %r to %s", title, recipient)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Alert] delivery to %s failed: %s", recipient, exc)


async def notify_operator(
    title: str,
    detail: str,
    *,
    dedup_key: str,
    action: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """Email the operator about an actionable failure. Never raises.

    Returns True if an email was actually dispatched — for tests and callers that
    want to log the decision, not for control flow.
    """
    try:
        if not alerts_configured():
            logger.debug("[Alert] suppressed (not configured): %s", title)
            return False

        suppressed = await _claim_send(
            dedup_key, int(settings.ALERT_COOLDOWN_MINUTES or 60)
        )
        if suppressed is None:
            logger.info("[Alert] within cooldown, counted not sent: %s", dedup_key)
            return False

        await _deliver(title, _compose(title, detail, action, suppressed, extra))
        return True
    except Exception as exc:  # noqa: BLE001
        # The contract: monitoring never breaks the thing it monitors.
        logger.warning("[Alert] notify_operator failed for %r: %s", title, exc)
        return False


def notify_operator_bg(
    title: str,
    detail: str,
    *,
    dedup_key: str,
    action: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Fire-and-forget wrapper for hot paths.

    Callers are mid-pipeline and must not wait on SMTP. If there is no running
    loop (sync context) the alert is dropped with a log line rather than blocking
    — losing an alert is acceptable, stalling a run is not.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("[Alert] no running loop, dropped: %s", title)
        return
    task = loop.create_task(
        notify_operator(title, detail, dedup_key=dedup_key, action=action, extra=extra)
    )
    # Keep a reference so the task isn't garbage-collected mid-flight, and make
    # sure a failure inside it can never surface as "exception was never
    # retrieved" noise on the event loop.
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


_PENDING: set[asyncio.Task] = set()
