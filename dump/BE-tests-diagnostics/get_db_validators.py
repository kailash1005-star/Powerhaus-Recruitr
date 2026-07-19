import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import connect_to_mongo, close_mongo_connection, get_database

async def check_validators():
    await connect_to_mongo()
    db = await get_database()
    
    # Get collection info to see validators
    collections_info = await db.list_collections()
    cols = await collections_info.to_list(length=None)
    for col_info in cols:
        name = col_info["name"]
        options = col_info.get("options", {})
        validator = options.get("validator", None)
        print(f"Collection: {name}")
        if validator:
            import pprint
            pprint.pprint(validator)
        else:
            print("  No validator defined.")
        print("-" * 50)
        
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(check_validators())
