"""
Parsed Job Description Document Model (matching engine)

One `parsed_jds` doc per JD/roles document submitted via the "Run Matching"
button. We never mutate the existing `jobs` collection — this is the structured,
embedded representation used purely for candidate matching.
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from bson import ObjectId

from app.models.cv_candidates import CvEmbeddingMeta


class JdRequirements(BaseModel):
    """Structured requirements extracted from the JD by the LLM."""
    title: Optional[str] = None
    mustHaveSkills: List[str] = Field(default_factory=list)
    niceToHaveSkills: List[str] = Field(default_factory=list)
    minYears: Optional[float] = None
    location: Optional[str] = None
    seniority: Optional[str] = None
    responsibilities: List[str] = Field(default_factory=list)


class ParsedJdModel(BaseModel):
    """MongoDB document model for the `parsed_jds` collection."""
    id: Optional[str] = Field(None, alias="_id")

    sourceFileName: Optional[str] = None
    sourceJobId: Optional[str] = None     # link to an existing `jobs` doc if any
    rawText: Optional[str] = None         # Docling markdown of the JD
    requirements: Optional[JdRequirements] = None
    embedding: Optional[CvEmbeddingMeta] = None

    createdAt: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
