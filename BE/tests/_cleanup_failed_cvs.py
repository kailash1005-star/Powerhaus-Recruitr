"""Delete CVs that failed ingestion (e.g. parsed before Docling was available)."""
import asyncio
from app.database import connect_to_mongo, get_database, close_mongo_connection


async def go():
    await connect_to_mongo()
    db = await get_database()
    res = await db["cv_candidates"].delete_many({"status": "failed"})
    print("deleted failed CVs:", res.deleted_count)
    print("cv_candidates remaining:", await db["cv_candidates"].count_documents({}))
    await close_mongo_connection()


asyncio.run(go())
