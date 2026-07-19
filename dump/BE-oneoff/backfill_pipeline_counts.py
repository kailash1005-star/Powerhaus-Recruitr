"""Repair the denormalized candidate counts on every pipeline.

Why this exists: the Apify discovery path used to write only `candidateCount` on
the job entry, never `acceptedCount`/`rejectedCount` nor the pipeline rollup, so
pipelines with real candidates displayed "0 candidates" in the list UI. The code
now routes every writer through `recount_pipeline`, but existing documents keep
their stale counts until something writes to them again — this backfills them.

Safe to re-run: it only recomputes cached counts from the `candidates`
collection, which is the source of truth. It never touches candidate data.

    cd BE && python backfill_pipeline_counts.py
"""
import asyncio
import logging

from app.database import connect_to_mongo, get_collection
from app.services.candidate_pipeline import recount_pipeline

logging.basicConfig(level=logging.WARNING)


async def main() -> None:
    await connect_to_mongo()
    pipelines_col = await get_collection("candidatePipelines")

    repaired = 0
    total = 0
    async for doc in pipelines_col.find({}, {"companyName": 1, "totalCandidates": 1}):
        total += 1
        pipeline_id = str(doc["_id"])
        before = doc.get("totalCandidates") or 0
        counts = await recount_pipeline(pipeline_id)
        after = counts.get("totalCandidates", 0)
        if before != after:
            repaired += 1
            print(f"  {doc.get('companyName', pipeline_id):<40} {before} -> {after}")

    print(f"\nScanned {total} pipeline(s); corrected {repaired}.")


if __name__ == "__main__":
    asyncio.run(main())
