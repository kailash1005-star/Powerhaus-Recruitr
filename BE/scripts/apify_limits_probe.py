"""Read-only probe: what does Apify actually allow this account, and how much is left?

Why this exists
---------------
The pipeline is throttled by Apify in four different ways, and each needs a
different fix — a dead token needs rotation, an actor run cap needs a plan
upgrade or a wait, and telling you the wrong one wastes your time. Before any
alerting can be wired we have to know the REAL ceilings, and nobody currently
does: ``APIFY_ENRICH_BATCH=10`` and ``JOB_ENRICH_SELECTION_MAX`` were both tuned
against "free tier caps runs" folklore rather than a number anyone measured.

So this script asks Apify directly and prints what it says. It writes nothing,
runs no actors, and spends no credits — every endpoint here is a GET.

Design note: it prints the RAW JSON for each endpoint before any interpretation,
and every derived reading is guarded. An alerting threshold wired against a
field name I guessed would be worse than no threshold at all, because it would
report "you're fine" right up until the pipeline stops. If a field is missing,
the probe says so loudly instead of defaulting to a comfortable number.

Run from BE/ with the venv python:
    venv/Scripts/python.exe scripts/apify_limits_probe.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import requests  # noqa: E402

API = "https://api.apify.com/v2"
TIMEOUT = 20

# Keys we HOPE carry the ceilings, checked defensively. Apify has renamed these
# across API versions, so we look for any of them and report which one hit —
# that is exactly the uncertainty this probe exists to remove.
_LIMIT_KEY_CANDIDATES = (
    "maxMonthlyUsageUsd", "monthlyUsageCreditsUsd", "maxMonthlyActorComputeUnits",
    "maxConcurrentActorJobs", "maxActorMemoryGbytes", "maxMonthlyActorRuns",
    "maxMonthlyResults", "dataRetentionDays",
)
_USAGE_KEY_CANDIDATES = (
    "monthlyUsageUsd", "monthlyActorComputeUnits", "actorMemoryGbytes",
    "monthlyActorRuns", "monthlyResults",
)


def _get(path: str, token: str, params: Optional[Dict[str, Any]] = None) -> tuple[int, Any]:
    """GET one endpoint. Returns (status_code, parsed_or_text). Never raises."""
    try:
        resp = requests.get(
            f"{API}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
            timeout=TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — a probe must report, not crash
        return 0, f"request failed: {exc}"
    try:
        return resp.status_code, resp.json()
    except Exception:  # noqa: BLE001
        return resp.status_code, resp.text[:2000]


def _unwrap(payload: Any) -> Any:
    """Apify wraps most responses in {"data": ...}."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


# ``/users/me`` returns the account's live proxy password in plain text. This
# output gets pasted into docs and terminals, so scrub credentials before any
# printing — a diagnostic that leaks a secret is a bug, not a convenience.
_SECRET_KEYS = {"password", "token", "apiKey", "api_key", "secret", "proxyPassword"}


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if k in _SECRET_KEYS and isinstance(v, str) else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _dump(label: str, status: int, payload: Any, *, full: bool = False) -> None:
    print(f"\n--- {label}  [HTTP {status}] ---")
    if status == 401:
        print("  !! 401 UNAUTHORIZED — this token is dead or revoked.")
        print("     This is the case the codebase currently cannot detect at all.")
        return
    if status == 403:
        print("  !! 403 FORBIDDEN — token lacks permission for this endpoint.")
        return
    if status != 200:
        print(f"  !! unexpected status. Body: {str(payload)[:800]}")
        return
    text = json.dumps(_redact(payload), indent=2, default=str, ensure_ascii=False)
    print(text if full or len(text) <= 4000 else text[:4000] + "\n  ... (truncated)")


def _scan_keys(obj: Any, wanted: tuple[str, ...], path: str = "") -> Dict[str, Any]:
    """Recursively find any of `wanted` anywhere in the payload, recording where.

    Recursive because the nesting depth is precisely what we don't know yet.
    """
    found: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else k
            if k in wanted and not isinstance(v, (dict, list)):
                found[here] = v
            found.update(_scan_keys(v, wanted, here))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            found.update(_scan_keys(v, wanted, f"{path}[{i}]"))
    return found


def main() -> int:
    from app.config import settings

    token = settings.APIFY_TOKEN
    if not token:
        print("APIFY_TOKEN is not set in BE/.env — nothing to probe.")
        return 1

    actors = [
        ("search", settings.APIFY_SEARCH_ACTOR),
        ("profile", settings.APIFY_PROFILE_ACTOR),
        ("company", settings.APIFY_COMPANY_ACTOR),
    ]

    print(f"Probing Apify with token ...{token[-6:]}  (read-only, no actor runs, no credits)")

    # ── 1. Identity / plan ────────────────────────────────────────────────
    _rule("1. ACCOUNT  GET /users/me")
    status, payload = _get("/users/me", token)
    _dump("users/me", status, payload)
    if status == 401:
        print("\nToken is invalid — stopping here. Rotate APIFY_TOKEN and re-run.")
        return 2
    me = _unwrap(payload) if status == 200 else {}
    if isinstance(me, dict):
        plan = me.get("plan") or {}
        print("\n  Readings:")
        print(f"    username : {me.get('username')}")
        print(f"    plan     : {plan.get('id') or plan.get('name') or '(not reported)'}")
        if isinstance(plan, dict) and plan:
            print(f"    plan keys: {sorted(plan.keys())}")

    # ── 2. The ceilings ───────────────────────────────────────────────────
    _rule("2. LIMITS + CURRENT USAGE  GET /users/me/limits")
    status, payload = _get("/users/me/limits", token)
    _dump("users/me/limits", status, payload, full=True)
    if status == 200:
        data = _unwrap(payload)
        limits = _scan_keys(data, _LIMIT_KEY_CANDIDATES)
        usage = _scan_keys(data, _USAGE_KEY_CANDIDATES)
        print("\n  Readings — CEILINGS found:")
        print("    (none of the expected keys matched)" if not limits else "")
        for k, v in sorted(limits.items()):
            print(f"    {k:55s} = {v}")
        print("\n  Readings — CURRENT USAGE found:")
        print("    (none of the expected keys matched)" if not usage else "")
        for k, v in sorted(usage.items()):
            print(f"    {k:55s} = {v}")
        if not limits:
            print("\n  !! No recognised ceiling keys. Read the raw JSON above and pick")
            print("     the right paths by hand — do NOT guess a threshold.")

    # ── 3. Monthly usage detail ───────────────────────────────────────────
    _rule("3. MONTHLY USAGE  GET /users/me/usage/monthly")
    status, payload = _get("/users/me/usage/monthly", token)
    _dump("users/me/usage/monthly", status, payload)

    # ── 4. Per-actor detail ───────────────────────────────────────────────
    _rule("4. ACTORS IN USE  GET /acts/{id}")
    for label, actor_id in actors:
        api_id = actor_id.replace("/", "~")  # Apify path form: user~actor
        status, payload = _get(f"/acts/{api_id}", token)
        print(f"\n### {label}: {actor_id}")
        if status != 200:
            _dump(actor_id, status, payload)
            continue
        act = _unwrap(payload)
        if not isinstance(act, dict):
            _dump(actor_id, status, payload)
            continue
        pricing = act.get("pricingInfos") or act.get("pricingInfo") or []
        print(f"    name          : {act.get('name')}")
        print(f"    isPublic      : {act.get('isPublic')}")
        print(f"    isDeprecated  : {act.get('isDeprecated')}")
        print(f"    pricing model : {json.dumps(pricing, default=str)[:600] if pricing else '(not reported)'}")
        stats = act.get("stats") or {}
        if stats:
            print(f"    stats         : {json.dumps(stats, default=str)[:400]}")

    # ── 5. Actual run history — the real consumption signal ───────────────
    _rule("5. RUN HISTORY  GET /actor-runs  (most recent 200)")
    status, payload = _get("/actor-runs", token, {"desc": "1", "limit": 200})
    if status != 200:
        _dump("actor-runs", status, payload)
    else:
        data = _unwrap(payload)
        items = data.get("items", []) if isinstance(data, dict) else []
        print(f"\n  {len(items)} recent runs returned (total reported: "
              f"{data.get('total') if isinstance(data, dict) else '?'})")
        by_actor: Counter[str] = Counter()
        by_status: Counter[str] = Counter()
        for r in items:
            if not isinstance(r, dict):
                continue
            by_actor[str(r.get("actId"))] += 1
            by_status[str(r.get("status"))] += 1
        print("\n  Runs per actor id:")
        for k, v in by_actor.most_common():
            print(f"    {k:30s} {v}")
        print("\n  Runs per status:")
        for k, v in by_status.most_common():
            print(f"    {k:30s} {v}")
        if items and isinstance(items[0], dict):
            print("\n  Newest run (shape reference for the ledger):")
            keep = ("id", "actId", "status", "startedAt", "finishedAt",
                    "statusMessage", "usageTotalUsd")
            print(json.dumps({k: items[0].get(k) for k in keep}, indent=4, default=str))

    _rule("NEXT")
    print("""
  Copy the real numbers above into docs/engineering/APIFY_LIMITS.md, then wire
  the alert thresholds against the exact key PATHS reported under "Readings".
  Any ceiling that came back "(not reported)" must NOT get a guessed default —
  leave that check off and rely on the on-failure hook instead.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
