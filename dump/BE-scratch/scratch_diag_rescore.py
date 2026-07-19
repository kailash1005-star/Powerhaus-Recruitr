"""Re-score the 12 real payroll candidates with the fixed scorer. No API calls —
reuses the stored embeddings and the stored match run's similarity."""
import asyncio, os, sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

RUN_ID = "6a58d23c206c9fe6c14f0f77"
JOB_ID = "6a58c9b76753c3ece2f47999"


async def main():
    from bson import ObjectId
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.services.matching_service import _score_candidate

    db = AsyncIOMotorClient(os.getenv("MONGODB_URI"))[os.getenv("DATABASE_NAME", "Job-Hunt")]
    run = await db.match_runs.find_one({"_id": ObjectId(RUN_ID)})
    reqs = run["requirements"]
    old = {c["candidateId"]: c for c in (run.get("analysis") or {}).get("candidates") or []}

    rows = []
    for cid, o in old.items():
        c = await db.candidates.find_one({"_id": ObjectId(cid)})
        profile = ((c or {}).get("apifyEnrichment") or {}).get("profile") or {}
        sim = o["breakdown"]["similarity"]
        score, sub, gaps, bd = _score_candidate(reqs, profile, sim)
        rows.append((score, o["score"], sub["skillCoverage"],
                     o["subscores"]["skillCoverage"], o.get("currentTitle") or "", gaps))

    rows.sort(key=lambda r: -r[0])
    print(f"{'NEW':>5} {'OLD':>5} | {'cov-new':>7} {'cov-old':>7} | title")
    print("-" * 92)
    for new, o, cn, co, t, gaps in rows:
        print(f"{new:5.1f} {o:5.1f} | {cn:7.1f} {co:7.1f} | {t[:46]}")
    print(f"\ntop gaps now: {rows[0][5]}")


if __name__ == "__main__":
    asyncio.run(main())
