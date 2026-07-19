import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import connect_to_mongo, close_mongo_connection, get_database

async def list_collections():
    await connect_to_mongo()
    db = await get_database()
    
    # List collections in current database
    cols = await db.list_collection_names()
    print("Collections in database:", cols)
    
    # Get database client to list all databases
    from app.database import client
    dbs = await client.list_database_names()
    print("All databases on cluster:", dbs)
    
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(list_collections())
