"""
CV Candidates Document Model (matching engine)

One `cv_candidates` doc per uploaded CV. Separate from the Apollo-sourced
`candidates` collection — this collection holds CVs ingested via the CV dump,
parsed by Docling and structured by a small LLM, then embedded for matching.

Dedup is by `contentHash` (sha256 of the raw file bytes) so re-uploading the
same file is a no-op.
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from bson import ObjectId


class CvExperienceEntry(BaseModel):
    company: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    summary: Optional[str] = None


class CvContact(BaseModel):
    """Contact info for outreach — kept separate so it can be excluded from the
    text we embed/score (bias control)."""
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin: Optional[str] = None


class CvProfile(BaseModel):
    """Structured projection of the CV. Only the fields the deterministic scorer
    and reasoning step need — the full clean markdown is on `markdown`."""
    fullName: Optional[str] = None
    location: Optional[str] = None
    totalYears: Optional[float] = None
    currentTitle: Optional[str] = None
    skills: List[str] = Field(default_factory=list)
    titles: List[str] = Field(default_factory=list)
    education: List[str] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    experience: List[CvExperienceEntry] = Field(default_factory=list)


class CvEmbeddingMeta(BaseModel):
    model: Optional[str] = None
    dim: Optional[int] = None
    version: Optional[str] = None
    # The vector itself is stored on the doc for the Mongo brute-force backend;
    # for Pinecone it lives in the index and this is just metadata.
    vector: Optional[List[float]] = None
    vectorId: Optional[str] = None  # id in the external vector store (Pinecone)


class CvCandidateModel(BaseModel):
    """MongoDB document model for the `cv_candidates` collection."""
    id: Optional[str] = Field(None, alias="_id")

    contentHash: str                      # sha256 of file bytes (unique)
    sourceFileName: Optional[str] = None
    batchId: Optional[str] = None         # groups one upload batch

    markdown: Optional[str] = None        # Docling output (clean text)
    profile: Optional[CvProfile] = None
    contact: Optional[CvContact] = None
    embedding: Optional[CvEmbeddingMeta] = None

    status: str = "pending"               # pending | parsed | embedded | failed
    error: Optional[str] = None

    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
