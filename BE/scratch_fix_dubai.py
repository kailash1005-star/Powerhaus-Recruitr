import asyncio
import os
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

from app.services.orchestrator import _run_phase3

async def main():
    uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("DATABASE_NAME", "Job-Hunt")
    client = AsyncIOMotorClient(uri)
    db = client[db_name]

    run_id = ObjectId("6a1334b0f212fb16e98f77c7")
    print(f"Targeting Dubai run ID: {run_id}")

    # Delete existing prospects for this run to avoid duplicates or orphans
    print("Deleting old orphaned prospects for this run...")
    del_result = await db.prospects.delete_many({"runId": run_id})
    print(f"Deleted {del_result.deleted_count} prospects.")

    # Run Phase 3
    print("Running Phase 3 logic (without email enrichment) for this run...")
    stats = await _run_phase3(
        run_oid=run_id,
        jobs_col=db.jobs,
        companies_col=db.companies,
        prospects_col=db.prospects,
        runs_col=db.runs,
    )
    print("Phase 3 complete! Stats:", stats)
    
if __name__ == "__main__":
    asyncio.run(main())
