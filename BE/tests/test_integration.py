import asyncio
import sys
import os

# Add BE to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import connect_to_mongo, close_mongo_connection, get_collection
from app.services.campaigns.orchestrator import process_run_background
from datetime import datetime
from bson import ObjectId

async def run_integration_test():
    print("Connecting to MongoDB...")
    await connect_to_mongo()
    
    runs_col = await get_collection("runs")
    jobs_col = await get_collection("jobs")
    
    # Define a small run config to keep test fast and low-cost
    run_config = {
        "searchTitles": ["python developer"],
        "searchLocations": ["chennai"],
        "hoursOld": 24,
        "resultsPerSearch": 20,
        "siteName": ["naukri"],
        "scrapeDescriptions": True,
        "maxDescriptions": 2,
        "minExperience": 0,
        "maxExperience": 5
    }
    
    run_doc = {
        "title": "Integration Test Run",
        "source": "naukri",
        "runStartedAt": datetime.utcnow(),
        "status": "active",
        "stats": {
            "totalJobsScraped": 0,
            "uniqueCompanies": 0,
            "acceptedCompanies": 0,
            "rejectedCompanies": 0,
            "totalProspects": 0,
        },
        "runConfig": run_config,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }
    
    print("Inserting run document...")
    result = await runs_col.insert_one(run_doc)
    run_id = str(result.inserted_id)
    print(f"Run created with ID: {run_id}")
    
    try:
        print("Starting background process run...")
        await process_run_background(run_id, run_config)
        
        print("Fetching updated run details...")
        updated_run = await runs_col.find_one({"_id": ObjectId(run_id)})
        print("\n=== RUN SUMMARY ===")
        print(f"Status: {updated_run.get('status')}")
        print(f"Stats: {updated_run.get('stats')}")
        print(f"Started: {updated_run.get('runStartedAt')}")
        print(f"Ended: {updated_run.get('runEndedAt')}")
        
        print("\n=== SAMPLE JOBS ===")
        cursor = jobs_col.find({"runId": ObjectId(run_id)}).limit(5)
        async for job in cursor:
            print(f"- Title: {job.get('title')}")
            print(f"  Company: {job.get('company')}")
            print(f"  Location: {job.get('location')}")
            print(f"  Quality Status: {job.get('qualityStatus')}")
            print(f"  Rejection Reason: {job.get('rejectionReason')}")
            print(f"  Salary: {job.get('jobDetails', {}).get('salary')}")
            print(f"  URL: {job.get('jobDetails', {}).get('jobUrl')}")
            print("-" * 40)
            
    except Exception as e:
        print(f"Error during integration test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Closing MongoDB connection...")
        await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(run_integration_test())
