"""Replay run 6a565abce9f6940efcd35b95's candidates through the reworked scorer.

Uses the JD vector already in parsed_jds and the CV vectors already in
cv_candidates, so it costs nothing and isolates the scoring change.
"""
import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from dotenv import load_dotenv

load_dotenv()
import sys; sys.path.insert(0, os.path.dirname(__file__))
from app.services.matching_service import _score_candidate  # noqa: E402
from app.services.pipeline_match_service import _cosine      # noqa: E402

RUN_ID = "6a565abce9f6940efcd35b95"


async def main():
    client = AsyncIOMotorClient(os.getenv("MONGODB_URI"))
    db = client[os.getenv("DATABASE_NAME", "Job-Hunt")]

    run = await db.match_runs.find_one({"_id": ObjectId(RUN_ID)})
    jd = await db.parsed_jds.find_one({"_id": ObjectId(run["jdId"])})
    reqs = jd["requirements"]
    jd_vec = jd["embedding"]["vector"]
    old = {r["candidateId"]: r["score"] for r in run["results"]}

    rows = []
    async for d in db.cv_candidates.find({"status": "embedded"}):
        p = d.get("profile") or {}
        vec = (d.get("embedding") or {}).get("vector")
        if not vec:
            continue
        sim = _cosine(jd_vec, vec)
        score, sub, gaps, bd = _score_candidate(reqs, p, sim)
        rows.append((score, sub, gaps, bd, p, str(d["_id"])))

    rows.sort(key=lambda r: -r[0])
    print(f"must-haves: {reqs['mustHaveSkills']}")
    print(f"minYears={reqs['minYears']}  location={reqs['location']!r}\n")
    print(f"{'NEW':>6} {'OLD':>6}  {'sem':>5} {'cov':>6} {'exp':>5} {'loc':>5}  title")
    print("-" * 100)
    for score, sub, gaps, bd, p, cid in rows:
        o = old.get(cid)
        print(f"{score:6.1f} {(f'{o:.1f}' if o else '   -'):>6}  "
              f"{sub['semantic']:5.1f} {sub['skillCoverage']:6.1f} {sub['experience']:5.1f} "
              f"{sub['location']:5.1f}  {(p.get('currentTitle') or '?')[:46]}")

    print("\n=== Breakdown of the previous #1 ===")
    top = next(r for r in rows if r[5] == "6a3b22bffeea415a713ca89e")
    bd = top[3]
    print(f"formula: {bd['formula']}")
    print(f"base={bd['base']}  ceiling={bd['ceiling']}  total={bd['total']}")
    print(f"cappedBy: {bd['cappedBy']}")
    for c in bd["components"]:
        flag = "" if c["applicable"] else "  [N/A — weight redistributed]"
        print(f"  {c['label']:26} value={c['value']:6.1f} w={c['weight']:.3f} "
              f"pts={c['points']:5.1f}/{c['maxPoints']:5.1f} lost={c['lost']:5.1f}{flag}")
        print(f"      {c['note']}")
        for e in c.get("skills") or []:
            print(f"        - {e['skill']:26} credit={e['credit']:.2f} "
                  f"[{e['method']}] via={e['via']!r}")


if __name__ == "__main__":
    asyncio.run(main())
