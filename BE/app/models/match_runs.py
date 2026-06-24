"""
Match Run Document Model (matching engine)

One `match_runs` doc per execution of the "Run Matching" button. Stores the
ranked result and the model/prompt versions so any ranking is reproducible and
auditable (a hard requirement for hiring AI).
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from bson import ObjectId


class MatchedCandidate(BaseModel):
    candidateId: str
    fullName: Optional[str] = None
    currentTitle: Optional[str] = None
    location: Optional[str] = None
    score: float = 0.0                      # 0..100 blended score
    subscores: Dict[str, float] = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list)   # why this candidate fits
    gaps: List[str] = Field(default_factory=list)      # what's missing
    contact: Dict[str, Any] = Field(default_factory=dict)  # for outreach/call


class ModelVersions(BaseModel):
    extract: Optional[str] = None
    embed: Optional[str] = None
    reason: Optional[str] = None


class MatchRunModel(BaseModel):
    """MongoDB document model for the `match_runs` collection."""
    id: Optional[str] = Field(None, alias="_id")

    jdId: Optional[str] = None
    jdTitle: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    candidatesConsidered: int = 0
    results: List[MatchedCandidate] = Field(default_factory=list)
    modelVersions: Optional[ModelVersions] = None
    vectorBackend: Optional[str] = None

    createdAt: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
