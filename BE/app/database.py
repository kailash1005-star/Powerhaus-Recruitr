"""
MongoDB Connection Setup
Uses motor (async MongoDB driver) for FastAPI
Handles connection pooling, session management, and lifecycle events
"""

from motor.motor_asyncio import AsyncIOMotorClient
from typing import AsyncGenerator
from app.config import settings

# Global MongoDB client instance
client: AsyncIOMotorClient | None = None
database = None


async def connect_to_mongo():
    """
    Connect to MongoDB on application startup
    Creates a connection pool that handles multiple concurrent requests
    """
    global client, database
    
    print(f"Connecting to MongoDB at {settings.MONGODB_URI}")
    
    client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        # Connection pool settings for 5+ concurrent users
        maxPoolSize=10,              # Maximum connections in pool
        minPoolSize=2,               # Minimum connections to keep alive
        maxIdleTimeMS=60000,         # Close idle connections after 60s
        serverSelectionTimeoutMS=5000,  # Server selection timeout
        connectTimeoutMS=10000,      # Connection timeout
        retryWrites=True,            # Retry writes on network errors
        w="majority"                 # Write concern: majority acknowledgment
    )
    
    database = client[settings.DATABASE_NAME]
    
    # Verify connection
    try:
        await database.command("ping")
        print(f"[OK] Connected to MongoDB database: {settings.DATABASE_NAME}")
    except Exception as e:
        print(f"[ERROR] MongoDB connection failed: {e}")
        raise

    # Relax prospects schema validator — pre-enrichment prospects may have empty
    # lastName / email which the original strict $jsonSchema rejects.
    try:
        await database.command({
            "collMod": "prospects",
            "validator": {
                "$jsonSchema": {
                    "bsonType": "object",
                    "required": ["_id", "firstName"],
                    "properties": {
                        "_id": {"bsonType": "objectId"},
                        "firstName": {"bsonType": "string"},
                        "lastName": {"bsonType": "string"},
                        "email": {"bsonType": ["string", "null"]},
                    },
                }
            },
            "validationLevel": "moderate",
        })
        print("[OK] Relaxed prospects schema validator")
    except Exception as e:
        # Collection may not exist yet — that's fine, we'll insert with default schema
        print(f"[WARN] Could not relax prospects validator (likely first run): {e}")

    # Rebuild prospects email index as a unique partial index — multiple
    # pre-enrichment prospects share email="" so a plain unique index breaks.
    try:
        prospects = database["prospects"]
        existing = await prospects.index_information()
        if "idx_email" in existing:
            await prospects.drop_index("idx_email")
        await prospects.create_index(
            "email",
            name="idx_email",
            unique=True,
            partialFilterExpression={"email": {"$type": "string", "$gt": ""}},
        )
        print("[OK] Rebuilt prospects email index as unique partial")
    except Exception as e:
        print(f"[WARN] Could not rebuild prospects email index: {e}")

    # candidatePipelines — one pipeline per company; lookups by companyId/jobs.jobId.
    try:
        pipelines = database["candidatePipelines"]
        await pipelines.create_index(
            "companyDomain", name="idx_companyDomain", unique=True,
            partialFilterExpression={"companyDomain": {"$type": "string", "$gt": ""}},
        )
        await pipelines.create_index("companyId", name="idx_companyId")
        await pipelines.create_index("jobs.jobId", name="idx_jobs_jobId")
        await pipelines.create_index([("companyName", "text")], name="idx_companyName_text")
        print("[OK] candidatePipelines indexes ensured")
    except Exception as e:
        print(f"[WARN] Could not create candidatePipelines indexes: {e}")

    # candidates — compound unique on (pipelineId, apolloId) so the same Apollo
    # person can appear in different pipelines but is deduped within one.
    try:
        candidates = database["candidates"]
        await candidates.create_index(
            [("pipelineId", 1), ("apolloId", 1)],
            name="idx_pipeline_apolloId", unique=True,
        )
        await candidates.create_index("pipelineId", name="idx_pipelineId")
        await candidates.create_index("sourceJobIds", name="idx_sourceJobIds")
        print("[OK] candidates indexes ensured")
    except Exception as e:
        print(f"[WARN] Could not create candidates indexes: {e}")
 
 
async def close_mongo_connection():
    """
    Close MongoDB connection on application shutdown
    Properly cleans up the connection pool
    """
    global client, database
    
    if client:
        client.close()
        print("[OK] MongoDB connection closed")
    
    client = None
    database = None
 
 
async def get_database():
    """
    Dependency function to get database session
    Use this in your route handlers:
        @router.get("/some-endpoint")
        async def some_endpoint(db = Depends(get_database)):
            ...
    """
    if database is None:
        raise RuntimeError("Database not connected. Call connect_to_mongo() first.")
    
    return database
 
 
async def get_collection(collection_name: str):
    """
    Dependency function to get a specific collection
    Use this in your route handlers:
        @router.get("/some-endpoint")
        async def some_endpoint(jobs = Depends(get_collection("jobs"))):
            ...
    """
    db = await get_database()
    return db[collection_name]
