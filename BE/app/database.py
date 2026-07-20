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
        serverSelectionTimeoutMS=30000,  # Server selection timeout
        connectTimeoutMS=20000,      # Connection timeout
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

    # users — one row per Auth0 identity, provisioned just-in-time on first
    # authenticated request. The unique index on auth0Sub is what makes that safe
    # under concurrency: two simultaneous first-calls race to upsert and the index
    # guarantees one row rather than two.
    try:
        from app.services.user_service import ensure_indexes as ensure_user_indexes
        await ensure_user_indexes(database)
        print("[OK] users indexes ensured")
    except Exception as e:
        print(f"[WARN] Could not create users indexes: {e}")

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

    # profileEnrichmentCache — Apify profile results keyed by public identifier
    # (_id). A TTL index expires entries a bit past the app-level re-enrich
    # window so stale profiles are re-fetched instead of served forever.
    try:
        cache = database["profileEnrichmentCache"]
        ttl_seconds = (settings.PROFILE_CACHE_TTL_DAYS + 1) * 86400
        await cache.create_index("fetchedAt", name="idx_cache_ttl", expireAfterSeconds=ttl_seconds)
        print("[OK] profileEnrichmentCache index ensured")
    except Exception as e:
        print(f"[WARN] Could not create profileEnrichmentCache index: {e}")

    # AI Engineer chat — threads listed by recency, messages fetched per thread.
    try:
        await database["chatThreads"].create_index("updatedAt", name="idx_chatThreads_updatedAt")
        await database["chatMessages"].create_index(
            [("threadId", 1), ("createdAt", 1)], name="idx_chatMessages_thread"
        )
        print("[OK] AI Engineer chat indexes ensured")
    except Exception as e:
        print(f"[WARN] Could not create chat indexes: {e}")

    # ── Matching engine collections ────────────────────────────────────────
    # cv_candidates — uploaded CVs; dedup by content hash; filter by status.
    try:
        cv = database["cv_candidates"]
        await cv.create_index("contentHash", name="idx_contentHash", unique=True)
        await cv.create_index("status", name="idx_status")
        await cv.create_index("batchId", name="idx_batchId")
        await cv.create_index("createdAt", name="idx_cv_createdAt")
        print("[OK] cv_candidates indexes ensured")
    except Exception as e:
        print(f"[WARN] Could not create cv_candidates indexes: {e}")

    # Atlas native vector + lexical search indexes — only when the production
    # ANN backend is selected (VECTOR_BACKEND=atlas). No-op otherwise. Requires
    # an Atlas M10+ tier; builds asynchronously on the Atlas side.
    try:
        from app.services.vector_store import ensure_atlas_indexes
        await ensure_atlas_indexes(database)
        if (settings.VECTOR_BACKEND or "mongo").lower() == "atlas":
            print("[OK] Atlas search indexes ensured")
    except Exception as e:
        print(f"[WARN] Could not ensure Atlas search indexes: {e}")

    # parsed_jds & match_runs — recency lookups / audit.
    try:
        await database["parsed_jds"].create_index("createdAt", name="idx_jd_createdAt")
        await database["match_runs"].create_index("createdAt", name="idx_run_createdAt")
        await database["match_runs"].create_index("jdId", name="idx_run_jdId")
        print("[OK] matching (parsed_jds/match_runs) indexes ensured")
    except Exception as e:
        print(f"[WARN] Could not create matching indexes: {e}")

    # ── Outreach CRM collections ───────────────────────────────────────────
    # outreach_messages — read model; one per (tenant, dedupeKey). Filter by
    # (audience, status); sort by lastActivityAt.
    try:
        msgs = database["outreach_messages"]
        await msgs.create_index(
            [("tenantId", 1), ("dedupeKey", 1)], name="idx_tenant_dedupe", unique=True,
        )
        await msgs.create_index([("tenantId", 1), ("audience", 1), ("status", 1)], name="idx_audience_status")
        await msgs.create_index([("tenantId", 1), ("email", 1)], name="idx_outreach_email")
        await msgs.create_index("lastActivityAt", name="idx_lastActivityAt")
        # outreach_events — append-only; unique providerEventId = idempotency.
        evs = database["outreach_events"]
        await evs.create_index("providerEventId", name="idx_providerEventId", unique=True)
        await evs.create_index("messageId", name="idx_event_messageId")
        await evs.create_index("occurredAt", name="idx_event_occurredAt")
        print("[OK] outreach (messages/events) indexes ensured")
    except Exception as e:
        print(f"[WARN] Could not create outreach indexes: {e}")


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
