"""Apify failure classification and account preflight.

Why this module exists
----------------------
Apify can stop the pipeline in ways that need OPPOSITE responses, and until now
they were indistinguishable:

  * a dead token       → rotate APIFY_TOKEN
  * an exhausted plan  → upgrade or wait for the cycle reset; rotating the token
                         changes NOTHING (same account, same $5)

Telling an operator to rotate a key that is working is worse than saying nothing:
it costs them a trip to the Apify console and leaves the real cause untouched. So
classification is the product here, not the alert plumbing.

The auth gap this closes
------------------------
An expired or revoked token was detected NOWHERE. Both actor call sites wrapped
every failure as ``ApifyRunFailed`` ("transient — retry once"), and none of the
three quota-marker lists contained ``401``, ``403``, ``unauthorized`` or
``token``. The observable result: the Broadener treats a dead token as "no
candidates matched", widens the filters, and burns its whole retry budget on
searches that were never actually run.

Where the marker lists went
---------------------------
There were three, in ``apify_profile_service``, ``apify_search_service`` and
``candidate_pipeline``. They disagreed — only one contained "free user run limit"
— so the classification you got depended on which layer happened to see the
string first. They are unified here; the others import from this module.

Measured limits, not assumed ones
---------------------------------
``docs/engineering/APIFY_LIMITS.md`` records what the account actually reports
(probe: ``BE/scripts/apify_limits_probe.py``). Two measurements shaped this file:

  * There is NO monthly run-count ceiling on this plan — the only wall is
    ``maxMonthlyUsageUsd`` ($5). So "free user run limit reached" is classified as
    a SPEND block, not as a separate run cap, and no run-count threshold exists
    to be guessed at.
  * The billing cycle runs 22nd→21st, not a calendar month, and it is read from
    the API rather than computed. A "monthly" total derived from
    ``datetime.now().month`` would be wrong for 21 days out of every 30.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from app.config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.apify.com/v2"
_PREFLIGHT_TIMEOUT_S = 10


# ── Kinds ────────────────────────────────────────────────────────────────────
# Deliberately NOT an enum of every possible failure: each kind exists only
# because it implies a DIFFERENT operator action. Two failures that need the
# same fix belong to the same kind.

KIND_AUTH = "auth"                    # rotate the token
KIND_SPEND = "spend"                  # plan/credit exhausted — rotation won't help
KIND_RATE_LIMIT = "rate_limit"        # too fast; back off (NOT the same as spend)
KIND_NOT_CONFIGURED = "not_configured"
KIND_TRANSIENT = "transient"          # retry is reasonable

# Non-transient kinds: a retry loop should stop, and the operator must act.
ACTIONABLE_KINDS = frozenset({KIND_AUTH, KIND_SPEND, KIND_NOT_CONFIGURED})


@dataclass(frozen=True)
class Classification:
    kind: str
    summary: str
    action: str

    @property
    def is_actionable(self) -> bool:
        """True when a human must do something — the gate for alerting.

        Transient failures and rate limits self-resolve; waking someone for them
        is how an alert channel gets muted, and a muted channel is worse than
        none.
        """
        return self.kind in ACTIONABLE_KINDS


# ── Markers ──────────────────────────────────────────────────────────────────
# The union of the three former lists. Matched case-insensitively against the
# vendor's own message text, which is the only signal available when the failure
# arrives as a SUCCEEDED run carrying an explanatory statusMessage (Apify's way
# of reporting an account refusal) rather than as an HTTP error.

SPEND_MARKERS = (
    # observed live, recorded in docs/engineering/SOURCING_FRICTION_ANALYSIS.md
    "free user run limit",
    "run limit reached",
    # plan / credit exhaustion
    "monthly limit", "monthly usage", "plan limit", "usage limit", "item limit",
    "quota", "credit", "out of credits", "not enough",
    "payment required", "upgrade", "upgrade your plan", "upgrade to a paid plan",
    "limited to", "exceeded",
)

AUTH_MARKERS = (
    "unauthorized", "invalid token", "authentication", "not authenticated",
    "invalid api key", "token is invalid", "forbidden", "insufficient permission",
)

RATE_LIMIT_MARKERS = ("rate limit exceeded", "too many requests", "rate-limited")


def _contains(haystack: str, markers: tuple[str, ...]) -> bool:
    return any(m in haystack for m in markers)


def status_code_of(exc: BaseException) -> Optional[int]:
    """HTTP status from an apify-client error, if it carries one.

    Read by duck-typing rather than importing ``apify_client.errors``: the pin is
    loose (``>=1.7.0``) and that module does not exist in older versions, so an
    import would turn a classification miss into an ImportError crash. 3.x sets
    ``.status_code`` on ``ApifyApiError`` and dispatches typed subclasses
    (UnauthorizedError/ForbiddenError/RateLimitError) that all inherit it.
    """
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def classify(error: Any, *, status_code: Optional[int] = None) -> Classification:
    """Classify a vendor failure into an operator action.

    ``error`` may be an exception or a message string — failures reach us both
    ways (raised by the client, or embedded in a dataset error row / run
    statusMessage).

    Status code wins over text when present: it is structured vendor truth,
    whereas the message is prose that can contain a misleading word (a "quota"
    mentioned inside a 401 body would otherwise mask a dead token).
    """
    if isinstance(error, BaseException) and status_code is None:
        status_code = status_code_of(error)

    text = str(error or "").lower()

    if status_code in (401, 403):
        return Classification(
            kind=KIND_AUTH,
            summary=f"Apify rejected the token (HTTP {status_code}).",
            action="Rotate APIFY_TOKEN: Apify Console -> Settings -> API & Integrations, "
                   "then update BE/.env (and the Cloud Run env var) and restart.",
        )
    if status_code == 429:
        return Classification(
            kind=KIND_RATE_LIMIT,
            summary="Apify rate-limited us (HTTP 429) after the client's own retries.",
            action="No action needed unless this repeats — reduce parallelism if it does.",
        )

    if _contains(text, AUTH_MARKERS):
        return Classification(
            kind=KIND_AUTH,
            summary="Apify reported an authentication failure.",
            action="Rotate APIFY_TOKEN: Apify Console -> Settings -> API & Integrations, "
                   "then update BE/.env (and the Cloud Run env var) and restart.",
        )
    if _contains(text, RATE_LIMIT_MARKERS):
        return Classification(
            kind=KIND_RATE_LIMIT,
            summary="Apify rate-limited the request.",
            action="Transient — it retries. Reduce parallelism if it persists.",
        )
    if _contains(text, SPEND_MARKERS):
        return Classification(
            kind=KIND_SPEND,
            summary="Apify refused the run — the account's plan/credit limit is reached.",
            action="This is an ACCOUNT limit, so rotating the token will NOT help. "
                   "Upgrade the Apify plan or wait for the usage cycle to reset "
                   "(see docs/engineering/APIFY_LIMITS.md for the cycle dates).",
        )
    if not settings.APIFY_TOKEN:
        return Classification(
            kind=KIND_NOT_CONFIGURED,
            summary="APIFY_TOKEN is not set.",
            action="Add APIFY_TOKEN to BE/.env — LinkedIn sourcing cannot run without it.",
        )

    return Classification(
        kind=KIND_TRANSIENT,
        summary="Apify run failed for an unrecognised reason.",
        action="Usually transient. If it repeats, check https://status.apify.com "
               "and the run log in the Apify Console.",
    )


async def alert_if_actionable(
    error: Any, *, where: str, extra: Optional[Dict[str, Any]] = None
) -> bool:
    """Classify a failure and alert the operator only if a human must act.

    The gate matters as much as the alert. Transient failures and rate limits are
    expected background noise in any scraping pipeline; emailing them is how the
    channel gets muted, and a muted channel is worse than none. Only ``auth``,
    ``spend`` and ``not_configured`` reach the inbox.

    The dedup key is the KIND, not the call site — one dead token breaks every
    job of every run, and the operator needs to be told once about the token, not
    once per job. ``where`` therefore travels as context inside the mail body.

    Never raises: callers are exception handlers.
    """
    try:
        verdict = classify(error)
        if not verdict.is_actionable:
            return False

        from app.services.operations import alerts

        titles = {
            KIND_AUTH: "Apify token rejected — LinkedIn sourcing is down",
            KIND_SPEND: "Apify plan limit reached — LinkedIn sourcing has stopped",
            KIND_NOT_CONFIGURED: "APIFY_TOKEN is not set — LinkedIn sourcing disabled",
        }
        return await alerts.notify_operator(
            titles.get(verdict.kind, "Apify failure"),
            f"{verdict.summary}\n\nDetected in: {where}\nVendor message: {str(error)[:400]}",
            dedup_key=f"apify:{verdict.kind}",
            action=verdict.action,
            extra=extra,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ApifyHealth] alert_if_actionable failed at %s: %s", where, exc)
        return False


def is_spend_error(msg: Any) -> bool:
    """Back-compat shim for the call sites that only asked 'was this a plan block?'

    Replaces the three separate ``_is_quota_error`` helpers.
    """
    return classify(msg).kind == KIND_SPEND


# ── Preflight ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AccountHealth:
    """A reading of the Apify account. ``ok`` False means we could not read it,
    which is NOT the same as the account being unhealthy — never alert on that
    alone, or every network blip pages the operator."""
    ok: bool
    token_valid: Optional[bool] = None
    usage_usd: Optional[float] = None
    max_usd: Optional[float] = None
    cycle_starts: Optional[str] = None
    cycle_ends: Optional[str] = None
    detail: str = ""

    @property
    def cycle_start_dt(self) -> Optional[datetime]:
        """Cycle start as a datetime, for querying our own ledger.

        Kept as the API's own boundary rather than a computed month: this
        account's cycle runs 22nd->21st, so a calendar-month window would be
        wrong for 21 days out of every 30.
        """
        if not self.cycle_starts:
            return None
        try:
            return datetime.fromisoformat(self.cycle_starts.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            return None

    @property
    def pct_used(self) -> Optional[float]:
        if self.usage_usd is None or not self.max_usd:
            return None
        return round(100.0 * self.usage_usd / self.max_usd, 1)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "tokenValid": self.token_valid,
            "usageUsd": self.usage_usd,
            "maxUsd": self.max_usd,
            "pctUsed": self.pct_used,
            "cycleEnds": self.cycle_ends,
            "detail": self.detail,
        }


def _read_account_sync() -> AccountHealth:
    """One GET against /users/me/limits. Costs nothing and runs no actor.

    Field paths are the ones the live probe returned, not remembered ones:
      data.limits.maxMonthlyUsageUsd
      data.current.monthlyUsageUsd
      data.monthlyUsageCycle.endAt
    Each is read defensively — a shape change must degrade to "cannot read",
    never to a falsely reassuring number.
    """
    if not settings.APIFY_TOKEN:
        return AccountHealth(ok=False, detail="APIFY_TOKEN is not set")

    try:
        resp = requests.get(
            f"{API_BASE}/users/me/limits",
            headers={"Authorization": f"Bearer {settings.APIFY_TOKEN}"},
            timeout=_PREFLIGHT_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — a probe must report, not raise
        return AccountHealth(ok=False, detail=f"could not reach Apify: {exc}")

    if resp.status_code in (401, 403):
        return AccountHealth(
            ok=True, token_valid=False,
            detail=f"Apify rejected the token (HTTP {resp.status_code})",
        )
    if resp.status_code != 200:
        return AccountHealth(ok=False, detail=f"unexpected HTTP {resp.status_code}")

    try:
        data = (resp.json() or {}).get("data") or {}
        limits = data.get("limits") or {}
        current = data.get("current") or {}
        cycle = data.get("monthlyUsageCycle") or {}
        max_usd = limits.get("maxMonthlyUsageUsd")
        usage_usd = current.get("monthlyUsageUsd")
        return AccountHealth(
            ok=True,
            token_valid=True,
            usage_usd=float(usage_usd) if isinstance(usage_usd, (int, float)) else None,
            max_usd=float(max_usd) if isinstance(max_usd, (int, float)) else None,
            cycle_starts=cycle.get("startAt"),
            cycle_ends=cycle.get("endAt"),
            detail="",
        )
    except Exception as exc:  # noqa: BLE001
        return AccountHealth(ok=False, detail=f"unreadable response: {exc}")


async def read_account() -> AccountHealth:
    """Async wrapper — ``requests`` is blocking and this runs on the event loop."""
    return await asyncio.to_thread(_read_account_sync)


# ── Per-actor breakdown ──────────────────────────────────────────────────────

# Apify's own run history is purged after `dataRetentionDays` (7 on this plan)
# while the billing cycle is ~30 days, so the vendor CANNOT answer "what did each
# actor cost me this cycle". Our `cost_events` ledger can: every actor call
# already records service="apify" with the vendor's real usageTotalUsd. So this
# reads the existing ledger rather than adding a second one.
_OPERATION_LABELS = {
    "profile_search": "LinkedIn people search",
    "profile_scrape": "LinkedIn profile enrichment",
    "company_scrape": "LinkedIn company lookup",
}


async def spend_by_actor(since: Optional[Any] = None) -> Dict[str, Dict[str, Any]]:
    """Per-operation Apify spend from the cost ledger, for diagnostics in alerts.

    ``since`` should be the cycle start reported by the API — never a computed
    calendar month, because this account's cycle runs 22nd->21st.
    Best-effort: returns {} on any failure, since a breakdown is a nice-to-have
    inside an alert whose headline number comes from the vendor directly.
    """
    try:
        from app.database import get_collection

        match: Dict[str, Any] = {"service": "apify"}
        if since is not None:
            match["createdAt"] = {"$gte": since}
        col = await get_collection("cost_events")
        rows = col.aggregate([
            {"$match": match},
            {"$group": {
                "_id": "$operation",
                "costUsd": {"$sum": "$costUsd"},
                "calls": {"$sum": 1},
                "units": {"$sum": "$quantity"},
            }},
            {"$sort": {"costUsd": -1}},
        ])
        out: Dict[str, Dict[str, Any]] = {}
        async for r in rows:
            op = str(r.get("_id") or "unknown")
            out[op] = {
                "label": _OPERATION_LABELS.get(op, op),
                "costUsd": round(float(r.get("costUsd") or 0.0), 4),
                "calls": int(r.get("calls") or 0),
                "units": r.get("units"),
            }
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ApifyHealth] per-actor spend breakdown unavailable: %s", exc)
        return {}


# ── Preflight ────────────────────────────────────────────────────────────────

async def preflight(*, source: str = "startup") -> Dict[str, Any]:
    """Check the account BEFORE work depends on it, and alert if it won't hold.

    This is the proactive half: on-failure hooks tell you a run has already been
    lost, while this can say "you have 12% left" before one starts.

    Two conditions alert, and only two:
      * the token is rejected  -> rotate it
      * spend is over the warn threshold -> upgrade or wait for the cycle reset

    A failure to READ the account is deliberately NOT an alert. Apify being
    briefly unreachable is not an operator problem, and paging someone for it is
    how a channel gets muted. It is logged and reported on /health instead.

    Never raises: callers include the startup hook, which must not be able to
    refuse boot.
    """
    health = await read_account()

    if not health.ok:
        logger.info("[ApifyHealth] preflight (%s) inconclusive: %s", source, health.detail)
        return health.as_dict()

    from app.services.operations import alerts

    if health.token_valid is False:
        verdict = classify("unauthorized", status_code=401)
        await alerts.notify_operator(
            "Apify token rejected — LinkedIn sourcing is down",
            "Apify is refusing our API token, so every LinkedIn search and profile "
            "enrichment will fail until it is replaced. Runs will complete but "
            "return no candidates.",
            dedup_key="apify:auth",
            action=verdict.action,
            extra={"checkedBy": source, "detail": health.detail},
        )
        logger.error("[ApifyHealth] preflight (%s): TOKEN REJECTED", source)
        return health.as_dict()

    pct = health.pct_used
    threshold = int(settings.APIFY_USAGE_WARN_PCT or 0)
    if threshold and pct is not None and pct >= threshold:
        # Scoped to THIS cycle. Unscoped it sums all history, which produced a
        # breakdown of ~$11.86 sitting next to "$0.23 of $5.00" — incoherent, and
        # alarming in the wrong direction. If the boundary is unknown, show no
        # breakdown rather than a misleading one: the headline number comes from
        # the vendor and stands on its own.
        cycle_start = health.cycle_start_dt
        breakdown = await spend_by_actor(since=cycle_start) if cycle_start else {}
        await alerts.notify_operator(
            f"Apify usage at {pct}% of the plan limit",
            f"Apify spend this cycle is ${health.usage_usd:.2f} of "
            f"${health.max_usd:.2f} ({pct}%). When it reaches the cap, searches "
            f"and enrichment stop returning data.",
            dedup_key="apify:spend",
            action="This is an ACCOUNT limit — rotating APIFY_TOKEN will NOT help. "
                   "Upgrade the Apify plan, or wait for the usage cycle to reset.",
            extra={
                "cycleEnds": health.cycle_ends,
                "checkedBy": source,
                # Deliberately labelled an ESTIMATE and kept separate from the
                # headline. Our meter falls back to a flat per-search price when
                # the vendor does not report actual usage, so this total runs
                # high (measured ~11x on a live cycle) and will NOT reconcile with
                # the figure above. It answers "which actor is eating the budget",
                # not "what am I billed" — the vendor number is authoritative.
                **{f"est. share ({v['label']})": f"~${v['costUsd']}"
                   for v in breakdown.values()},
                **({"note": "'est. share' is our own meter and reads high; "
                            "the cycle total above is Apify's own figure."}
                   if breakdown else {}),
            },
        )
        logger.warning("[ApifyHealth] preflight (%s): usage %.1f%% >= %d%%",
                       source, pct, threshold)
    else:
        logger.info("[ApifyHealth] preflight (%s): ok, usage %s%% of $%s",
                    source, pct, health.max_usd)
    return health.as_dict()
