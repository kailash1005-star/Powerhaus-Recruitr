import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def main():
    uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("DATABASE_NAME", "Job-Hunt")
    client = AsyncIOMotorClient(uri)
    db = client[db_name]

    print("Fetching last 5 runs...")
    cursor = db.runs.find(sort=[("createdAt", -1)]).limit(5)
    async for run in cursor:
        print(f"Run ID: {run['_id']}")
        print(f"Run Config: {run.get('runConfig', {})}")
        print("----")

if __name__ == "__main__":
    asyncio.run(main())
