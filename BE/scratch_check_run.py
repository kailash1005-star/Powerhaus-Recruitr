import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pprint import pprint

load_dotenv()

async def main():
    uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("DATABASE_NAME", "Job-Hunt")
    client = AsyncIOMotorClient(uri)
    db = client[db_name]

    print("Fetching last run...")
    last_run = await db.runs.find_one(sort=[("createdAt", -1)])
    if not last_run:
        print("No runs found.")
        return

    print("Last Run ID:", last_run["_id"])
    run_id = last_run["_id"]

    jobs = await db.jobs.find({"runId": run_id}).to_list(10)
    print("\nJobs sample:")
    for j in jobs:
        print(f"JobID: {j['_id']}, CompanyID: {j.get('companyId')}, CompanyName: {j.get('company')}")

    prospects = await db.prospects.find({"runId": run_id}).to_list(10)
    print("\nProspects sample:")
    for p in prospects:
        print(f"ProspectID: {p['_id']}, JobID: {p.get('jobId')}, CompanyID: {p.get('companyId')}")

if __name__ == "__main__":
    asyncio.run(main())
