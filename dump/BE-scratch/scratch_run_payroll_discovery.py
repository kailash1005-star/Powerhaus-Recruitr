"""FULL end-to-end for the payroll test job:

  role spec -> requirement-driven search -> constrained broadening -> gate
  -> enrich ONLY survivors -> match -> scored results.

Resets only the throwaway payroll test pipeline created in this session.
"""
import asyncio, os, sys, json
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

PIPELINE_ID = "6a58c9b76753c3ece2f4799a"
JOB_ID = "6a58c9b76753c3ece2f47999"
MAX_ITEMS = 12


async def main():
    from bson import ObjectId
    from app.database import connect_to_mongo, get_database
    from app.services.candidate_pipeline import _discover_candidates_for_job
    from app.services.sourcing import build_brief, propose_strategy
    from app.services.pipeline_match_service import start_pipeline_match

    await connect_to_mongo()
    db = await get_database()

    d = await db.candidates.delete_many({"sourceJobIds": JOB_ID})
    await db.candidatePipelines.update_one(
        {"_id": ObjectId(PIPELINE_ID), "jobs.jobId": JOB_ID},
        {"$set": {"jobs.$.searchStatus": "awaiting_input", "jobs.$.searchAttempts": [],
                  "jobs.$.prescreen": None, "jobs.$.enrichStatus": None}},
    )
    print(f"[reset] removed {d.deleted_count} prior test candidate(s)\n", flush=True)

    # ── 1. requirement-driven aim ──
    brief = await build_brief(PIPELINE_ID, JOB_ID)
    print(f"[brief] must-haves: {brief.mustHaveSkills}")
    print(f"[brief] minYears={brief.minYears} seniority={brief.seniorityHint!r}\n", flush=True)

    strategy = await propose_strategy(brief)
    filters = strategy.filters.to_search_input()
    ladder = [s.model_dump(mode="json") for s in strategy.broadeningLadder]
    print(f"[aim] {json.dumps(filters, ensure_ascii=False)}\n", flush=True)

    # ── 2. search + gate + enrich survivors ──
    await _discover_candidates_for_job(
        PIPELINE_ID, JOB_ID, filters, MAX_ITEMS,
        auto_broaden=True, hints=None, ladder=ladder,
    )

    pipe = await db.candidatePipelines.find_one({"_id": ObjectId(PIPELINE_ID)})
    entry = next(j for j in pipe["jobs"] if j["jobId"] == JOB_ID)

    print("=== ATTEMPTS ===", flush=True)
    for a in entry.get("searchAttempts") or []:
        f = a["filters"]
        print(f"  {a['attempt']}. [{a['action']}] -> {a['resultCount']} result(s)"
              f"{' ERROR: ' + a['error'][:60] if a.get('error') else ''}")
        print(f"     titles={json.dumps(f.get('currentJobTitles') or [], ensure_ascii=False)}")
        print(f"     loc={f.get('locations')} sen={f.get('seniorityLevel')} "
              f"yrs={f.get('yearsOfExperience')} fn={f.get('function')}")

    ps = entry.get("prescreen") or {}
    print(f"\n=== GATE === total={ps.get('total')} kept={ps.get('kept')} "
          f"dropped-before-enrichment={ps.get('dropped')}")
    for x in ps.get("droppedSamples") or []:
        print(f"    DROP {x.get('score')}  {str(x.get('title'))[:58]}")

    print("\n=== STORED ===", flush=True)
    kept_ids = []
    async for c in db.candidates.find({"sourceJobIds": JOB_ID}):
        p = c.get("prescreen") or {}
        if c.get("isAccepted"):
            kept_ids.append(str(c["_id"]))
        print(f"  {'KEPT' if c.get('isAccepted') else 'DROP'} {str(p.get('score')):>5} "
              f"{'enriched' if c.get('isApifyEnriched') else 'not-enriched':13} "
              f"{str(c.get('currentTitle'))[:48]}")

    if not kept_ids:
        print("\nNo candidates survived the gate — nothing to match.")
        return

    # ── 3. match the survivors ──
    print(f"\n=== MATCHING {len(kept_ids)} survivor(s) ===", flush=True)
    run_id = await start_pipeline_match(
        pipeline_id=PIPELINE_ID, job_id=JOB_ID, candidate_ids=kept_ids, return_top=5)
    for _ in range(240):
        await asyncio.sleep(2)
        run = await db.match_runs.find_one({"_id": ObjectId(run_id)})
        if run and run.get("status") in ("completed", "failed"):
            break
    print(f"match run {run_id} -> {run.get('status')} {run.get('error') or ''}")
    print(f"UI: http://localhost:3000/matching/{run_id}")
    for r in (run.get("analysis") or {}).get("candidates") or []:
        bd = r.get("breakdown") or {}
        print(f"  {r['score']:5.1f}  {str(r.get('currentTitle'))[:42]:42} "
              f"| {bd.get('formula','')}")
        if bd.get("cappedBy"):
            print(f"         capped: {bd['cappedBy'][:80]}")


if __name__ == "__main__":
    asyncio.run(main())
