"""Calibrate the pre-screen threshold against REAL sourced candidates.

Arm A: the 39 people actually sourced for the real "SAP Consultant" job, scored
       against that job's own search titles. These are mostly RIGHT — a good gate
       must keep them.
Arm B: the same people scored against a PAYROLL role's titles. These are mostly
       WRONG — a good gate must drop them.
The threshold has to separate the two.
"""
import asyncio, os, sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

JOB_ID = "6a5652ede9f6940efcd35b1e"

SAP_TARGETS = ["SAP S/4HANA Consultant", "SAP Consultant", "SAP Transformation Consultant",
               "SAP S/4HANA Migration Consultant", "SAP Berater S/4HANA", "SAP Berater"]
SAP_REQS = {"title": "SAP Consultant",
            "mustHaveSkills": ["SAP", "S/4HANA-Migrationen", "Transformationen", "SAP-Module"]}

PAY_TARGETS = ["Entgeltabrechner", "Personalsachbearbeiter Entgeltabrechnung",
               "Payroll Specialist", "Lohnbuchhalter", "HR Payroll Specialist"]
PAY_REQS = {"title": "Sachbearbeiter Entgeltabrechnung",
            "mustHaveSkills": ["SAP HR3", "Entgeltabrechnung", "Arbeitsrecht",
                               "Sozialversicherungsrecht", "Lohnsteuerrecht"]}


async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.services.prescreen_service import score_profile

    db = AsyncIOMotorClient(os.getenv("MONGODB_URI"))[os.getenv("DATABASE_NAME", "Job-Hunt")]
    titles = []
    async for c in db.candidates.find({"sourceJobIds": JOB_ID}, {"currentTitle": 1, "displayName": 1}):
        titles.append(c.get("currentTitle") or "")

    print(f"{len(titles)} real candidates sourced for the SAP Consultant job\n")
    print(f"{'SAPfit':>7} {'PAYfit':>7}   title")
    print("-" * 86)
    sap_scores, pay_scores = [], []
    for t in sorted(set(titles)):
        p = {"currentTitle": t}
        a = score_profile(p, requirements=SAP_REQS, target_titles=SAP_TARGETS)["score"]
        b = score_profile(p, requirements=PAY_REQS, target_titles=PAY_TARGETS)["score"]
        sap_scores.append(a)
        pay_scores.append(b)
        print(f"{a:7.1f} {b:7.1f}   {t[:70]}")

    def pct(xs, th):
        return 100 * sum(1 for x in xs if x >= th) / max(1, len(xs))

    print("\n  threshold |  keeps SAP people (want HIGH) | keeps them for PAYROLL (want LOW)")
    for th in (15, 20, 25, 30, 34, 40, 50):
        print(f"    {th:>5}   |  {pct(sap_scores, th):24.0f}% | {pct(pay_scores, th):26.0f}%")


if __name__ == "__main__":
    asyncio.run(main())


async def show_drops():
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.services.prescreen_service import score_profile
    db = AsyncIOMotorClient(os.getenv("MONGODB_URI"))[os.getenv("DATABASE_NAME", "Job-Hunt")]
    print("\n=== Real SAP-sourced candidates the gate would DROP for their OWN job ===")
    async for c in db.candidates.find({"sourceJobIds": JOB_ID}, {"currentTitle": 1, "displayName": 1}):
        t = c.get("currentTitle") or ""
        v = score_profile({"currentTitle": t}, requirements=SAP_REQS, target_titles=SAP_TARGETS)
        if v["score"] < 50:
            print(f"  {v['score']:5.1f}  {c.get('displayName')!r:32} title={t!r}")
