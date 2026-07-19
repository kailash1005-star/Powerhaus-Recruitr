import asyncio
import sys
import os
from bson import ObjectId

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import connect_to_mongo, close_mongo_connection, get_collection

async def check_run():
    await connect_to_mongo()
    runs_col = await get_collection("runs")
    jobs_col = await get_collection("jobs")
    
    target_id = "6a0d908d8a41b49e2573bb24"
    run = await runs_col.find_one({"_id": ObjectId(target_id)})
    if run:
        print(f"FOUND RUN: {run}")
        jobs_count = await jobs_col.count_documents({"runId": ObjectId(target_id)})
        print(f"Jobs under this runId: {jobs_count}")
    else:
        print(f"RUN {target_id} NOT FOUND!")
        
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(check_run())
