"""
Candidate LinkedIn Profile Download API
Endpoints for downloading and retrieving LinkedIn candidate profiles.

This is Step 1 of the candidate sourcing feature: given a LinkedIn profile URL,
download the complete profile and return structured data.
"""

import logging
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import get_database
from app.security.tenant import TenantContext, tenant_scope
from app.services.apify_profile_service import ApifyCostGuard, ApifyNotConfigured
from app.services.candidate_enrichment import enrich_candidates
from app.services.linkedin_profile_service import (
    extract_profile_slug,
    get_linkedin_profile_service,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request / Response schemas ───────────────────────────────────────────────

class ProfileDownloadRequest(BaseModel):
    """Request body for LinkedIn profile download."""
    linkedin_url: str = Field(
        ...,
        description="LinkedIn profile URL (e.g. https://www.linkedin.com/in/satyanadella/)",
        examples=["https://www.linkedin.com/in/satyanadella/"],
    )


class ExperienceItem(BaseModel):
    title: str = ""
    company_name: str = ""
    company_logo_url: str = ""
    location: str = ""
    description: str = ""
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None


class EducationItem(BaseModel):
    school_name: str = ""
    degree_name: str = ""
    field_of_study: str = ""
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None


class CertificationItem(BaseModel):
    name: str = ""
    authority: str = ""
    url: str = ""


class ContactInfo(BaseModel):
    email: str = ""
    phone_numbers: list[str] = []
    websites: list[str] = []
    twitter: list = []


class ProfileDownloadResponse(BaseModel):
    """Structured LinkedIn profile data returned after download."""
    success: bool
    message: str
    profile: Optional[dict] = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post(
    "/download-profile",
    response_model=ProfileDownloadResponse,
    summary="Download a LinkedIn candidate profile",
    description=(
        "Given a LinkedIn profile URL, downloads the candidate's full profile "
        "including experience, education, skills, certifications, and contact info. "
        "This is Step 1 of the candidate sourcing pipeline."
    ),
)
async def download_linkedin_profile(body: ProfileDownloadRequest):
    """Download a LinkedIn profile given a URL.

    Returns the complete structured profile data including:
    - Personal info (name, headline, summary, location)
    - Experience history
    - Education
    - Skills, certifications, languages
    - Contact info (when accessible)
    """
    linkedin_url = body.linkedin_url.strip()
    if not linkedin_url:
        raise HTTPException(status_code=400, detail="linkedin_url is required")

    # Validate URL format
    slug = extract_profile_slug(linkedin_url)
    if not slug:
        raise HTTPException(
            status_code=400,
            detail=f"Could not extract a LinkedIn profile slug from: {linkedin_url}",
        )

    try:
        service = get_linkedin_profile_service()
        profile = service.download_profile(linkedin_url)

        if profile is None:
            return ProfileDownloadResponse(
                success=False,
                message=f"Could not download profile for '{slug}'. "
                        "The profile may be private or the LinkedIn session may have expired.",
                profile=None,
            )

        return ProfileDownloadResponse(
            success=True,
            message=f"Successfully downloaded profile for {profile.get('full_name', slug)}",
            profile=profile,
        )

    except Exception as e:
        logger.error("Profile download endpoint error: %s", e, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"LinkedIn API error: {str(e)}",
        )


@router.get(
    "/download-profile",
    response_model=ProfileDownloadResponse,
    summary="Download a LinkedIn profile (GET)",
    description="Convenience GET endpoint — same as POST but accepts the URL as a query param.",
)
async def download_linkedin_profile_get(
    linkedin_url: str = Query(
        ...,
        description="LinkedIn profile URL",
        examples=["https://www.linkedin.com/in/satyanadella/"],
    ),
):
    """GET variant for quick testing / browser access."""
    return await download_linkedin_profile(ProfileDownloadRequest(linkedin_url=linkedin_url))


# ── Candidate enrichment (Apify deep profile → matcher-ready enrichedData) ────

class EnrichRequest(BaseModel):
    """Select candidates to enrich — EITHER candidateIds OR pipelineId (+jobId)."""
    candidateIds: Optional[List[str]] = Field(
        default=None, description="Explicit candidate _id list to enrich"
    )
    pipelineId: Optional[str] = Field(
        default=None, description="Enrich all candidates in this pipeline"
    )
    jobId: Optional[str] = Field(
        default=None, description="Narrow pipeline enrichment to one source job"
    )
    force: bool = Field(
        default=False,
        description="Re-enrich even already-enriched candidates (bypasses cache read)",
    )


class EnrichResponse(BaseModel):
    success: bool
    message: str
    summary: dict


@router.post(
    "/enrich",
    response_model=EnrichResponse,
    summary="Enrich selected candidates with deep LinkedIn profile data",
    description=(
        "On-demand enrichment: fetches full LinkedIn profiles (skills, experience "
        "with descriptions, education, certifications) via the Apify actor for the "
        "selected candidates, merges with their Apollo record, and writes "
        "matcher-ready enrichedData. Within-TTL profiles are served from cache "
        "(no re-charge)."
    ),
)
async def enrich_candidates_endpoint(
    body: EnrichRequest,
    ctx: TenantContext = Depends(tenant_scope),
    db=Depends(get_database),
):
    """Enrich a selected set of candidates (by id list or pipeline/job)."""
    if not body.candidateIds and not body.pipelineId:
        raise HTTPException(status_code=400, detail="provide candidateIds or pipelineId")

    # Tenant guard: enrichment triggers a PAID scrape of a candidate's LinkedIn
    # profile, so a caller must only ever enrich their own tenant's candidates.
    if not ctx.is_admin:
        if body.pipelineId:
            try:
                pipe = await db["candidatePipelines"].find_one(
                    {"_id": ObjectId(body.pipelineId)}, {"tenantId": 1})
            except Exception:
                pipe = None
            if not ctx.owns(pipe):
                raise HTTPException(status_code=404, detail="pipeline not found")
        if body.candidateIds:
            try:
                oids = [ObjectId(cid) for cid in body.candidateIds]
            except Exception:
                raise HTTPException(status_code=400, detail="invalid candidate id")
            owned = await db["candidates"].count_documents(
                {"_id": {"$in": oids}, "tenantId": ctx.tenant_id})
            if owned != len(set(oids)):
                # Some requested candidates aren't this tenant's (or are unstamped).
                raise HTTPException(status_code=404, detail="candidate not found")
    try:
        summary = await enrich_candidates(
            candidate_ids=body.candidateIds,
            pipeline_id=body.pipelineId,
            job_id=body.jobId,
            force=body.force,
        )
    except ApifyCostGuard as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ApifyNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error("Candidate enrichment failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Enrichment error: {e}")

    return EnrichResponse(
        success=True,
        message=(
            f"Enriched {summary['enriched']} "
            f"(cached {summary['cached']}, not_found {summary['not_found']}, "
            f"skipped {summary['skipped']})"
        ),
        summary=summary,
    )
