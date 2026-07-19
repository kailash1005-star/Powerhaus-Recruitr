"""One-off: remove any test CVs (example.com addresses) from the corpus."""
import asyncio
import re

from app.database import connect_to_mongo, get_database, close_mongo_connection


async def go():
    await connect_to_mongo()
    db = await get_database()
    res = await db["cv_candidates"].delete_many({"contact.email": {"$regex": re.compile(r"@example\.com$")}})
    print("deleted test CVs:", res.deleted_count)
    print("cv_candidates remaining:", await db["cv_candidates"].count_documents({}))
    await close_mongo_connection()


asyncio.run(go())
