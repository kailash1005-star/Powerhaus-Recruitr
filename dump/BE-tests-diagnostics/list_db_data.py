import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import connect_to_mongo, close_mongo_connection, get_collection

async def list_db_data():
    await connect_to_mongo()
    runs_col = await get_collection("runs")
    jobs_col = await get_collection("jobs")
    
    print("\n--- RUNS ---")
    cursor = runs_col.find().sort("createdAt", -1)
    async for run in cursor:
        print(f"ID: {run['_id']} | Title: {run.get('title')} | Source: {run.get('source')} | Status: {run.get('status')} | Stats: {run.get('stats')}")
        # Count jobs for this run
        jobs_count = await jobs_col.count_documents({"runId": run["_id"]})
        good_jobs_count = await jobs_col.count_documents({"runId": run["_id"], "qualityStatus": "good"})
        poor_jobs_count = await jobs_col.count_documents({"runId": run["_id"], "qualityStatus": "poor"})
        print(f"  -> Total jobs in DB: {jobs_count} (good={good_jobs_count}, poor={poor_jobs_count})")
        
    print("\n--- ALL JOBS BOARD ---")
    boards = await jobs_col.distinct("boardName")
    print("Boards in jobs collection:", boards)
    for b in boards:
        count = await jobs_col.count_documents({"boardName": b})
        print(f"  - {b}: {count}")

    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(list_db_data())
