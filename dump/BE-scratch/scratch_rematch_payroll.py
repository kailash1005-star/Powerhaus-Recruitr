"""Re-match the payroll job's already-enriched candidates with match-scoring-3.

No Apify: every candidate is already Apify-enriched, so the pipeline matcher skips
enrichment and only re-embeds/re-scores/re-reasons.
"""
import asyncio, os, sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

PIPELINE_ID = "6a58c9b76753c3ece2f4799a"
JOB_ID = "6a58c9b76753c3ece2f47999"


async def main():
    from bson import ObjectId
    from app.database import connect_to_mongo, get_database
    from app.services.pipeline_match_service import start_pipeline_match

    await connect_to_mongo()
    db = await get_database()

    ids = [str(c["_id"]) async for c in db.candidates.find(
        {"sourceJobIds": JOB_ID, "isAccepted": True}, {"_id": 1})]
    print(f"re-matching {len(ids)} already-enriched candidate(s)\n", flush=True)

    run_id = await start_pipeline_match(
        pipeline_id=PIPELINE_ID, job_id=JOB_ID, candidate_ids=ids, return_top=5)

    for _ in range(300):
        await asyncio.sleep(2)
        run = await db.match_runs.find_one({"_id": ObjectId(run_id)})
        if run and run.get("status") in ("completed", "failed"):
            break

    print(f"run {run_id} -> {run.get('status')} {run.get('error') or ''}")
    print(f"UI: http://localhost:3000/matching/{run_id}\n")
    for r in (run.get("analysis") or {}).get("candidates") or []:
        print(f"  {r['score']:5.1f}  cov={r['subscores']['skillCoverage']:5.1f}  "
              f"{str(r.get('currentTitle'))[:44]:44} gaps={r.get('gaps')}")
    top = ((run.get("analysis") or {}).get("candidates") or [None])[0]
    if top:
        sk = [c for c in top["breakdown"]["components"] if c["key"] == "skillCoverage"][0]
        print(f"\nTOP — {top.get('fullName')}:")
        for e in sk["skills"]:
            print(f"   {e['skill']:26} credit={e['credit']:.2f} [{e['method']}] via={e['via']!r}")
        print("\n   reasons:")
        for x in top.get("reasons") or []:
            print(f"     - {x}")


if __name__ == "__main__":
    asyncio.run(main())
