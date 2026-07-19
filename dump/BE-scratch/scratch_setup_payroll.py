"""Create a NEW pipeline + job carrying the German SAP payroll JD.

Additive only — the existing SAP pipelines and their sourced candidates are left
untouched. Re-runnable: reuses the job/pipeline if they already exist.
"""
import asyncio, os, sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from bson import ObjectId  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

TITLE = "Sachbearbeiter Entgeltabrechnung (SAP HR3)"
LOCATION = "Frankfurt, Germany"
COMPANY = "Payroll Test Co"

JD = """Aufgabenprofil:
Mitarbeit bei der Lohn- und Gehaltsabrechnung
Pflege und Kontrolle von Personalstammdaten und Bewegungsdaten im SAP-System
Durchführung der monatlichen Abrechnungen
Überprüfung der Abrechnungsergebnisse und Bearbeitung von Rückfragen

Bearbeitung administrativer Personalvorgänge
Anlegen, Ändern und Pflegen von Personaldaten (z.B. Eintritte, Austritte, Änderungen von Adressen oder Bankverbindungen)
Verwaltung von Abwesenheiten (Urlaub, Krankheit, Elternzeit)

Unterstützung im Melde- und Bescheinigungswesen
Erstellung von Bescheinigungen für Mitarbeiter (z.B. Arbeitsbescheinigungen, Verdienstbescheinigungen)
Meldungen an Sozialversicherungsträger, Finanzamt und andere Behörden über SAP HR3

Datenpflege und -auswertung
Pflege abrechnungsrelevanter Daten im SAP HR3
Unterstützung bei Auswertungen und Berichten (z.B. Monatsabschlüsse, Statistiken)

Ansprechpartner für Mitarbeiteranliegen
Beantwortung von Rückfragen zu Lohn- und Gehaltsabrechnung sowie Personaldaten
Unterstützung bei Fragen zu SAP Employee Self Service (ESS)

Unterstützung bei Prozessoptimierungen
Mitarbeit an der Weiterentwicklung und Optimierung von Payroll-Prozessen
Dokumentation von Arbeitsabläufen und Unterstützung bei Systemtests (z.B. nach Updates)
Qualifikationsprofil:
Kaufmännische oder steuerfachliche Ausbildung
Mehrjährige einschlägige Berufserfahrung, insbesondere in der Entgeltabrechnung
Sehr gute Kenntnisse in SAP HR3
Sehr gute Kenntnisse im Arbeits-, Sozialversicherungs- und Lohnsteuerrecht
Hohes Verantwortungsbewusstsein und Eigeninitiative
Eigenmotiviert und eigenständig sowie vorausschauend und mitdenkend
Hohe Diskretion und Verschwiegenheit
Ausgeprägte Dienstleistungsorientierung"""


async def main():
    db = AsyncIOMotorClient(os.getenv("MONGODB_URI"))[os.getenv("DATABASE_NAME", "Job-Hunt")]
    now = datetime.utcnow()

    job = await db.jobs.find_one({"title": TITLE, "source": "manual"})
    if job:
        job_id = str(job["_id"])
        print(f"reusing job {job_id}")
    else:
        job_id = str((await db.jobs.insert_one({
            "title": TITLE,
            "location": LOCATION,
            "boardName": "manual",
            "qualityStatus": "good",
            "jobDetails": {"description": JD},
            "source": "manual",
            "createdAt": now,
            "updatedAt": now,
        })).inserted_id)
        print(f"created job {job_id}")

    pipe = await db.candidatePipelines.find_one({"companyName": COMPANY})
    if pipe:
        pipeline_id = str(pipe["_id"])
        print(f"reusing pipeline {pipeline_id}")
    else:
        pipeline_id = str((await db.candidatePipelines.insert_one({
            "companyName": COMPANY,
            "companyDomain": "payroll-test.example",
            "companyIndustry": "",
            "companyLocation": "Germany",
            "source": "manual",
            "jobs": [],
            "totalCandidates": 0,
            "acceptedCount": 0,
            "rejectedCount": 0,
            "createdAt": now,
            "updatedAt": now,
        })).inserted_id)
        print(f"created pipeline {pipeline_id}")

    pipe = await db.candidatePipelines.find_one({"_id": ObjectId(pipeline_id)})
    if not any(j.get("jobId") == job_id for j in (pipe.get("jobs") or [])):
        await db.candidatePipelines.update_one(
            {"_id": ObjectId(pipeline_id)},
            {"$push": {"jobs": {
                "jobId": job_id, "jobTitle": TITLE, "jobLocation": LOCATION,
                "addedAt": now, "searchStatus": "awaiting_input", "lastSearchedAt": None,
                "candidateCount": 0, "acceptedCount": 0, "rejectedCount": 0,
                "appliedIndustryFallback": False, "searchError": None,
            }}, "$set": {"updatedAt": now}},
        )
        print("attached job to pipeline")

    print(f"\nPIPELINE_ID={pipeline_id}\nJOB_ID={job_id}")
    print(f"UI: http://localhost:3000/pipelines/{pipeline_id}")


if __name__ == "__main__":
    asyncio.run(main())
