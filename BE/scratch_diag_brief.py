"""Does the requirement now reach the Search Strategist?

Runs build_brief + propose_strategy against a REAL pipeline job and prints what
the search would aim at. Read-only apart from creating the job's role spec.
"""
import asyncio, os, sys, json
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

PIPELINE_ID = sys.argv[1] if len(sys.argv) > 1 else "6a56529be9f6940efcd35b1d"
JOB_ID = sys.argv[2] if len(sys.argv) > 2 else "6a5652ede9f6940efcd35b1e"


async def main():
    from app.database import connect_to_mongo
    from app.services.sourcing import build_brief, propose_strategy

    await connect_to_mongo()
    brief = await build_brief(PIPELINE_ID, JOB_ID)
    print("=" * 78)
    print("BRIEF HANDED TO THE STRATEGIST")
    print("=" * 78)
    print(f"  jobTitle       : {brief.jobTitle!r}")
    print(f"  jobLocation    : {brief.jobLocation!r}")
    print(f"  mustHaveSkills : {brief.mustHaveSkills}")
    print(f"  niceToHave     : {brief.niceToHaveSkills}")
    print(f"  minYears       : {brief.minYears}")
    print(f"  seniorityHint  : {brief.seniorityHint!r}")
    print(f"  jobDescription : {len(brief.jobDescription)} chars")

    strategy = await propose_strategy(brief)
    print()
    print("=" * 78)
    print("PROPOSED SEARCH")
    print("=" * 78)
    print(f"  interpretedRole : {strategy.interpretedRole}")
    print(f"  titleReasoning  : {strategy.titleReasoning}")
    print(f"  confidence      : {strategy.confidence}")
    f = strategy.filters
    print(f"  searchQuery     : {f.searchQuery!r}")
    print(f"  currentJobTitles: {json.dumps(f.currentJobTitles, ensure_ascii=False)}")
    print(f"  locations       : {f.locations}")
    print(f"  profileLanguages: {f.profileLanguages}")
    print(f"  seniorityLevel  : {f.seniorityLevel} | years={f.yearsOfExperience} | function={f.function}")
    for w in strategy.warnings:
        print(f"  ! warning       : {w}")
    print("  ladder:")
    for s in strategy.broadeningLadder:
        print(f"    {s.step}. [{s.action}] {s.detail}")


if __name__ == "__main__":
    asyncio.run(main())
