"""
Candidate Pipeline Document Model

A `candidatePipelines` doc represents one recruitment pipeline for a company.
The pipeline holds multiple jobs the recruiter wants to source candidates for.
Each embedded job has its own `searchStatus` that drives the background search
state machine (queued → running → completed/failed).
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from bson import ObjectId


class PipelineJobModel(BaseModel):
    """One job in a pipeline. Carries the search state for that job."""
    jobId: str
    jobTitle: str
    jobLocation: Optional[str] = ""
    addedAt: datetime
    # State machine for the background candidate search on this job:
    #   queued   — added but search hasn't picked it up yet
    #   running  — background task is actively searching
    #   completed — search finished, candidates inserted
    #   failed   — search errored (see searchError); user can rerun
    searchStatus: str = "queued"
    lastSearchedAt: Optional[datetime] = None
    candidateCount: int = 0
    acceptedCount: int = 0
    rejectedCount: int = 0
    appliedIndustryFallback: bool = False
    searchError: Optional[str] = None
    # One entry per executed search, including the agent-broadened retries a
    # zero-result search triggers — the timeline the UI shows to explain what the
    # agent tried and why. See services/sourcing/models.py::SearchAttempt.
    searchAttempts: List[dict] = Field(default_factory=list)


class CandidatePipelinesModel(BaseModel):
    """MongoDB document model for candidatePipelines collection."""
    id: Optional[str] = Field(None, alias="_id")
    # Reference to the company in `companies`. May be a synthetic record when
    # the recruiter created the pipeline for a company not already in the DB.
    companyId: Optional[str] = None
    companyName: str
    companyDomain: str
    companyIndustry: Optional[str] = ""
    # The industry name the company maps to in the recruiter's ICP — used as the
    # primary `person_industries[]` filter when searching candidates.
    matchedIndustry: Optional[str] = None
    companyLocation: Optional[str] = ""
    linkedinSlug: Optional[str] = None
    website: Optional[str] = ""
    # Whether this pipeline was created from a Run row ("run") or by the user
    # manually entering company details ("manual").
    source: str = "run"
    jobs: List[PipelineJobModel] = Field(default_factory=list)
    totalCandidates: int = 0
    acceptedCount: int = 0
    rejectedCount: int = 0
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
