"""
Companies API — minimal endpoints used by the candidate-pipeline UI.

GET /companies/{id}  — fetch a single company doc for modal prefill.
"""
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId

from app.database import get_database

router = APIRouter()


async def get_db():
    return await get_database()


@router.get("/{company_id}")
async def get_company(company_id: str, db=Depends(get_db)):
    try:
        oid = ObjectId(company_id)
    except Exception:
        raise HTTPException(400, "Invalid company id")
    doc = await db["companies"].find_one({"_id": oid})
    if not doc:
        raise HTTPException(404, "Company not found")
    doc["_id"] = str(doc["_id"])
    return doc
