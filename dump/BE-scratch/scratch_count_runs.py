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
    
    count = await db.runs.count_documents({})
    print(f"Total runs in database '{db_name}': {count}")

if __name__ == "__main__":
    asyncio.run(main())
