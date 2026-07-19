import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import connect_to_mongo, close_mongo_connection, get_collection

async def check_jobs():
    await connect_to_mongo()
    jobs_col = await get_collection("jobs")
    
    total_jobs = await jobs_col.count_documents({})
    print(f"Total jobs in collection: {total_jobs}")
    
    # List distinct board names
    boards = await jobs_col.distinct("boardName")
    print(f"Distinct board names: {boards}")
    
    # Get recent jobs
    print("Recent 5 jobs:")
    cursor = jobs_col.find().sort("createdAt", -1).limit(5)
    async for job in cursor:
        print(f"ID: {job['_id']} | Title: {job.get('title')} | Company: {job.get('company')} | Board: {job.get('boardName')} | CreatedAt: {job.get('createdAt')}")
        
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(check_jobs())
