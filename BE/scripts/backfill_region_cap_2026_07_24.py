"""One-off backfill (2026-07-24): apply the region-mismatch score cap and the
client-facing rejection copy to candidates sourced BEFORE those fixes deployed.

Context: Kastell's first sourcing run ("Network Engineer, Bamberg") surfaced
right-country/wrong-region candidates wearing uncapped 100s, and the sourcing
QA auditor wrote internal instrument language ("QA: wrong specialty — ...")
into client-visible rejectionReason strings. New runs are fixed in code
(candidate_pipeline._cap_region_mismatch, sourcing_qa_service); this brings the
already-persisted rows in line.

Idempotent: re-running changes nothing the second time.

Run from BE/ with the venv python:
    venv/Scripts/python.exe scripts/backfill_region_cap_2026_07_24.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main() -> None:
    from app.config import settings

    cli = AsyncIOMotorClient(settings.MONGODB_URI)
    db = cli[settings.DATABASE_NAME]
    cands = db["candidates"]
    cap = float(settings.SOURCING_REGION_MISMATCH_CAP or 70.0)

    # 1. Cap scores for kept right-country/wrong-region candidates.
    capped = 0
    async for c in cands.find({
        "prescreen.location.decision": "region_mismatch",
        "matchScore": {"$gt": cap},
        "matchScoreSource": {"$ne": "match_run"},  # never touch a real match score
    }):
        reasons = list(c.get("matchReasons") or [])
        note = f"Outside the requested region — score capped at {cap:g}."
        if note not in reasons:
            reasons.append(note)
        await cands.update_one(
            {"_id": c["_id"]},
            {"$set": {"matchScore": int(cap), "matchReasons": reasons[:3],
                      "prescreen.score": cap}},
        )
        capped += 1
    print(f"score-capped {capped} region-mismatch candidates at {cap:g}")

    # 2. Reword internal QA rejection copy already shown to clients.
    reworded = 0
    async for c in cands.find({"rejectionReason": {"$regex": r"^QA: wrong specialty"}}):
        flag = c.get("sourcingQaFlag") or {}
        specialty = (flag.get("likelyActualSpecialty")
                     or flag.get("reason") or "a different specialty")
        await cands.update_one(
            {"_id": c["_id"]},
            {"$set": {"rejectionReason": f"Different specialty: {specialty}"}},
        )
        reworded += 1
    print(f"reworded {reworded} rejection reasons to client-facing copy")

    cli.close()


if __name__ == "__main__":
    asyncio.run(main())
