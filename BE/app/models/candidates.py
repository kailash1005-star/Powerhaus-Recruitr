"""
Candidates Document Model

A `candidates` doc represents one person surfaced by an Apollo people-search
for a job in a candidate pipeline. The same Apollo person can exist in
multiple pipelines (separate docs), but is deduped within one pipeline via a
compound unique index on (pipelineId, apolloId).
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from datetime import datetime
from bson import ObjectId


class CandidateEmploymentEntry(BaseModel):
    """One trimmed past-role entry derived from Apollo's employment_history."""
    title: Optional[str] = None
    organizationName: Optional[str] = None
    organizationId: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    current: bool = False


class CandidateSocials(BaseModel):
    twitter: Optional[str] = None
    github: Optional[str] = None
    facebook: Optional[str] = None


class CandidateOrganizationSlim(BaseModel):
    """Slim snapshot of the candidate's current employer — see ``enrichedRaw``
    for the full 58-field organization object."""
    name: Optional[str] = None
    primaryDomain: Optional[str] = None
    industry: Optional[str] = None
    estimatedNumEmployees: Optional[int] = None
    foundedYear: Optional[int] = None
    hqCity: Optional[str] = None
    hqCountry: Optional[str] = None
    shortDescription: Optional[str] = None
    logoUrl: Optional[str] = None
    linkedinUrl: Optional[str] = None
    websiteUrl: Optional[str] = None


class CandidateEnrichedDataModel(BaseModel):
    """Structured projection of Apollo /people/match. Contains ONLY fields the
    endpoint actually returns — education / skills / summary / openToWork /
    candidate-level phone are intentionally absent (Apollo does not provide
    them on this endpoint). The full untouched payload is on ``enrichedRaw``.
    """
    email: Optional[str] = None
    emailStatus: Optional[str] = None
    personalEmails: List[str] = Field(default_factory=list)
    linkedinUrl: Optional[str] = None
    photoUrl: Optional[str] = None
    title: Optional[str] = None
    headline: Optional[str] = None
    seniority: Optional[str] = None
    functions: List[str] = Field(default_factory=list)
    departments: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    timeZone: Optional[str] = None
    employmentHistory: List[CandidateEmploymentEntry] = Field(default_factory=list)
    socials: Optional[CandidateSocials] = None
    organization: Optional[CandidateOrganizationSlim] = None


class CandidateRunHistoryEntry(BaseModel):
    """One entry per search run that surfaced this candidate."""
    runAt: datetime
    jobId: str
    isRerun: bool = False
    appliedIndustryFallback: bool = False


class CandidatesModel(BaseModel):
    """MongoDB document model for candidates collection."""
    id: Optional[str] = Field(None, alias="_id")
    pipelineId: str
    # All jobs in this pipeline that surfaced the same person. Grows on re-runs
    # and when a candidate matches multiple jobs.
    sourceJobIds: List[str] = Field(default_factory=list)

    # Apollo identity
    apolloId: str
    externalLinkedinUrl: Optional[str] = ""

    # Person basics
    firstName: str = "Unknown"
    lastName: str = "Unknown"
    displayName: Optional[str] = None
    headline: Optional[str] = ""
    currentTitle: Optional[str] = ""
    currentCompany: Optional[str] = ""
    currentCompanyDomain: Optional[str] = ""
    location: Optional[str] = ""

    # Match scoring (computed at insert time, never re-scored)
    matchScore: int = 0
    matchReasons: List[str] = Field(default_factory=list)

    # Acceptance / rejection — driven by recruiter clicks
    isAccepted: bool = True
    rejectionReason: Optional[str] = None
    decidedAt: Optional[datetime] = None

    # Manual enrichment (Apollo /people/match — consumes credits on Apollo side)
    isEnriched: bool = False
    enrichedAt: Optional[datetime] = None
    enrichedData: Optional[CandidateEnrichedDataModel] = None
    # FULL untouched Apollo /people/match payload, kept verbatim for audit. We
    # store the whole `person` object plus the top-level `request_id` so anything
    # added to the API later is captured automatically.
    enrichedRaw: Optional[Dict[str, Any]] = None
    enrichedSource: Optional[str] = None  # "apollo:/people/match" etc.

    runHistory: List[CandidateRunHistoryEntry] = Field(default_factory=list)
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
