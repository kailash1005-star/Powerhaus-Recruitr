"""A/B: what the Strategist proposes WITHOUT vs WITH the structured requirements.

The "before" arm reproduces the old build_brief exactly — job title, location,
company, and the raw jobDetails.description, with mustHaveSkills/minYears/
seniorityHint left empty (they were only ever filled from recruiter hints).
"""
import asyncio, os, sys, json
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

PIPELINE_ID = "6a56529be9f6940efcd35b1d"
JOB_ID = "6a5652ede9f6940efcd35b1e"


def show(tag, s):
    f = s.filters
    print(f"\n--- {tag}")
    print(f"  searchQuery     : {f.searchQuery!r}")
    print(f"  currentJobTitles: {json.dumps(f.currentJobTitles, ensure_ascii=False)}")
    print(f"  profileLanguages: {f.profileLanguages}")
    print(f"  seniority={f.seniorityLevel} years={f.yearsOfExperience} function={f.function}")
    print(f"  confidence      : {s.confidence}")


async def main():
    from app.database import connect_to_mongo
    from app.services.sourcing import build_brief, propose_strategy

    await connect_to_mongo()

    new = await build_brief(PIPELINE_ID, JOB_ID)

    old = new.model_copy(deep=True)
    old.mustHaveSkills = []
    old.niceToHaveSkills = []
    old.minYears = None
    old.seniorityHint = ""

    print(f"JD text both arms see: {len(new.jobDescription)} chars")
    print(f"BEFORE mustHaves: {old.mustHaveSkills}")
    print(f"AFTER  mustHaves: {new.mustHaveSkills}")

    show("BEFORE (no structured requirements)", await propose_strategy(old))
    show("AFTER  (requirement-driven)", await propose_strategy(new))


if __name__ == "__main__":
    asyncio.run(main())
