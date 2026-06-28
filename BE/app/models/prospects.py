"""
Prospects Document Model
Follows MongoDB schema from DB/Mongo.txt
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from bson import ObjectId


class ProspectDetailsModel(BaseModel):
    linkedinUrl: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None


class ProspectsModel(BaseModel):
    """MongoDB document model for prospects collection"""
    id: Optional[str] = Field(None, alias="_id")
    runId: Optional[str] = None
    companyId: Optional[str] = None
    apolloId: Optional[str] = None
    firstName: str
    lastName: str
    email: Optional[str] = ""
    title: Optional[str] = None
    seniority: Optional[str] = None
    industryName: Optional[str] = None
    isEnriched: bool = False
    mobileEnrichmentStatus: Optional[str] = None  # None | "pending" | "enriched"
    isAccepted: bool = True
    matchReasons: List[str] = Field(default_factory=list)
    rejectionReason: Optional[str] = None
    prospectDetails: Optional[ProspectDetailsModel] = None
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
