"""
API Router - Aggregates all v1 routers
"""
from fastapi import APIRouter
from app.api.v1 import icp, runs, analytics, jobs, pipelines, companies, agent, matching, outreach

api_router = APIRouter()

# Include sub-routers
api_router.include_router(icp.router, prefix="/icp", tags=["ICP"])
api_router.include_router(runs.router, prefix="/runs", tags=["Runs"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["Jobs"])
api_router.include_router(pipelines.router, prefix="/pipelines", tags=["Pipelines"])
api_router.include_router(companies.router, prefix="/companies", tags=["Companies"])
api_router.include_router(agent.router, prefix="/agent", tags=["AI Engineer"])
api_router.include_router(matching.router, prefix="/matching", tags=["Matching"])
api_router.include_router(outreach.router, prefix="/outreach", tags=["Outreach"])
