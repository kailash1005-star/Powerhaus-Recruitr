"""Re-screen the 12 stored payroll-search hits against the ROLE, not the
broadened aim. No Apify calls — pure re-evaluation of what is already stored."""
import asyncio, os, sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

JOB_ID = "6a58c9b76753c3ece2f47999"

# What the Strategist ORIGINALLY aimed at (attempt 1) — the role's real titles.
ORIGINAL_TITLES = ["Entgeltabrechnung Spezialist", "Payroll Specialist",
                   "Personalsachbearbeiter Entgeltabrechnung",
                   "Sachbearbeiter Lohnabrechnung", "Lohn- und Gehaltsbuchhalter"]
# What the Broadener drifted to (attempt 2) — a different job family.
BROADENED_TITLES = ["SAP Consultant", "Payroll Consultant", "HR Specialist",
                    "HR Consultant", "HR Administrator"]


async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.services.prescreen_service import score_profile

    db = AsyncIOMotorClient(os.getenv("MONGODB_URI"))[os.getenv("DATABASE_NAME", "Job-Hunt")]
    spec = await db.parsed_jds.find_one({"sourceJobId": JOB_ID})
    reqs = (spec or {}).get("requirements") or {}
    print(f"must-haves: {reqs.get('mustHaveSkills')}\n")

    print(f"{'BROADENED':>9} {'ROLE':>6}   title")
    print("-" * 78)
    kept_old = kept_new = total = 0
    async for c in db.candidates.find({"sourceJobIds": JOB_ID}, {"currentTitle": 1}):
        p = {"currentTitle": c.get("currentTitle") or ""}
        old = score_profile(p, requirements=reqs, target_titles=BROADENED_TITLES)["score"]
        new = score_profile(p, requirements=reqs, target_titles=ORIGINAL_TITLES)["score"]
        total += 1
        kept_old += old >= 25
        kept_new += new >= 25
        print(f"{old:9.1f} {new:6.1f}   {str(c.get('currentTitle'))[:58]}")

    print(f"\n  screening against the BROADENED aim : keeps {kept_old}/{total} -> "
          f"{kept_old} paid enrichments")
    print(f"  screening against the ROLE          : keeps {kept_new}/{total} -> "
          f"{kept_new} paid enrichments")


if __name__ == "__main__":
    asyncio.run(main())
