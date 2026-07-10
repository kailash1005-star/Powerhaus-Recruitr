"""
FastAPI Application Entry Point
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.router import api_router
from app.database import connect_to_mongo, close_mongo_connection
from app.config import settings
from app.startup_checks import log_matching_readiness, matching_readiness, outreach_readiness

app = FastAPI(
    title="Recruitment API",
    description="Job hunting and recruitment automation API",
    version="1.0.0",
    redirect_slashes=False
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include v1 routers
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Health check endpoint — includes matching-engine readiness so a
    misconfigured environment (wrong Python / missing parser deps) is visible."""
    return {"status": "healthy", "matching": matching_readiness(), "outreach": outreach_readiness()}

@app.on_event("startup")
async def startup_db_client():
    """Connect to MongoDB and verify the matching engine is runnable."""
    log_matching_readiness()
    await connect_to_mongo()
 
 
@app.on_event("shutdown")
async def shutdown_db_client():
    """Close MongoDB connection on shutdown"""
    await close_mongo_connection()
