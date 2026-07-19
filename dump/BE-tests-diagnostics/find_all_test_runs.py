import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import connect_to_mongo, close_mongo_connection, get_collection

async def find_runs():
    await connect_to_mongo()
    runs_col = await get_collection("runs")
    jobs_col = await get_collection("jobs")
    
    cursor = runs_col.find({"title": "Integration Test Run"})
    async for run in cursor:
        print(f"RUN: {run}")
        jobs_count = await jobs_col.count_documents({"runId": run["_id"]})
        print(f"  -> Jobs count in DB: {jobs_count}")
        
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(find_runs())
