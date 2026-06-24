"""
Pipeline Orchestrator
Background task entry-point that coordinates:
  Phase 1: Job scraping + title rejection + dedup + store          (jobspy_service / naukri_service)
  Phase 2: OpenAI company industry resolution on accepted jobs     (openai_company_service)
  Phase 3: Apollo prospect search (no enrichment) for targeted cos (apollo_service)
"""
import asyncio
from datetime import datetime
from typing import Any, Dict, List
import logging
import re
import urllib.parse

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.database import get_collection
from app.config import settings
from app.services.jobspy_service import scrape_and_store_jobs
from app.services.naukri_service import scrape_and_store_naukri_jobs
from app.services.openai_company_service import OpenAICompanyService
from app.services.apollo_service import ApolloService
from app.services.linkedin_service import LinkedInCompanyService, get_linkedin_service

logger = logging.getLogger(__name__)


async def process_run_background(run_id: str, run_config: Dict[str, Any]):
    """Background task called by POST /runs/start."""
    print(f"[Orchestrator] Starting run {run_id}")

    runs_col = await get_collection("runs")
    jobs_col = await get_collection("jobs")
    companies_col = await get_collection("companies")
    prospects_col = await get_collection("prospects")

    try:
        run_oid = ObjectId(run_id)
        now = datetime.utcnow()

        await runs_col.update_one(
            {"_id": run_oid},
            {"$set": {"status": "active", "currentPhase": "scraping", "updatedAt": now}},
        )

        run_doc = await runs_col.find_one({"_id": run_oid})
        source = run_doc.get("source", "jobspy") if run_doc else "jobspy"

        site_names = run_config.get("siteName", [])
        run_jobspy = "linkedin" in site_names
        run_naukri = "naukri" in site_names
        if not run_jobspy and not run_naukri:
            if source in ["naukri", "mixed"]:
                run_naukri = True
            if source in ["jobspy", "mixed"]:
                run_jobspy = True

        # ──────────────────────────────────────────────────────────────
        # Phase 1 — scrape + title-reject + dedup + store
        # ──────────────────────────────────────────────────────────────
        total_scraped = total_inserted = total_duplicates = 0
        total_accepted = total_rejected = 0

        if run_jobspy:
            jobspy_config = run_config.copy()
            jobspy_config["siteName"] = ["linkedin"]
            print(f"[Orchestrator] Running JobSpy scraper for: {jobspy_config['siteName']}")
            js_stats = await scrape_and_store_jobs(run_oid, jobspy_config, jobs_col)
            total_scraped += js_stats.get("total_scraped", 0)
            total_inserted += js_stats.get("inserted", 0)
            total_duplicates += js_stats.get("duplicates", 0)
            total_accepted += js_stats.get("accepted", 0)
            total_rejected += js_stats.get("rejected", 0)

        if run_naukri:
            print("[Orchestrator] Running Naukri scraper")
            nk_stats = await scrape_and_store_naukri_jobs(run_oid, run_config, jobs_col)
            total_scraped += nk_stats.get("total_scraped", 0)
            total_inserted += nk_stats.get("inserted", 0)
            total_duplicates += nk_stats.get("duplicates", 0)
            total_accepted += nk_stats.get("accepted", 0)
            total_rejected += nk_stats.get("rejected", 0)

        await runs_col.update_one(
            {"_id": run_oid},
            {
                "$set": {
                    "stats.totalJobsScraped": total_scraped,
                    "stats.inserted": total_inserted,
                    "stats.duplicates": total_duplicates,
                    "stats.acceptedJobs": total_accepted,
                    "stats.rejectedJobs": total_rejected,
                    "currentPhase": "companies",
                    "updatedAt": datetime.utcnow(),
                }
            },
        )
        print(f"[Orchestrator] Phase 1 done — scraped={total_scraped} accepted={total_accepted}")

        # ──────────────────────────────────────────────────────────────
        # Phase 2 — OpenAI company industry resolution
        # ──────────────────────────────────────────────────────────────
        target_industries: List[str] = list(run_config.get("targetIndustries") or [])
        custom_industries: List[str] = list(run_config.get("customIndustries") or [])
        # Merge user-added industries (treat both lists as the same target pool)
        all_target_industries = list({*(target_industries), *(custom_industries)})

        phase2_stats = await _run_phase2(
            run_oid=run_oid,
            target_industries=all_target_industries,
            jobs_col=jobs_col,
            companies_col=companies_col,
            runs_col=runs_col,
        )
        print(f"[Orchestrator] Phase 2 done — {phase2_stats}")

        # ──────────────────────────────────────────────────────────────
        # Phase 3 — Apollo prospect search for targeted companies
        # ──────────────────────────────────────────────────────────────
        phase3_stats = await _run_phase3(
            run_oid=run_oid,
            jobs_col=jobs_col,
            companies_col=companies_col,
            prospects_col=prospects_col,
            runs_col=runs_col,
        )
        print(f"[Orchestrator] Phase 3 done — {phase3_stats}")

        await runs_col.update_one(
            {"_id": run_oid},
            {
                "$set": {
                    "status": "completed",
                    "currentPhase": "done",
                    "runEndedAt": datetime.utcnow(),
                    "updatedAt": datetime.utcnow(),
                }
            },
        )
        print(f"[Orchestrator] Run {run_id} completed")

    except Exception as exc:
        print(f"[Orchestrator] Run {run_id} failed: {exc}")
        import traceback
        traceback.print_exc()
        await runs_col.update_one(
            {"_id": ObjectId(run_id)},
            {
                "$set": {
                    "status": "cancelled",
                    "currentPhase": "failed",
                    "error": str(exc),
                    "runEndedAt": datetime.utcnow(),
                    "updatedAt": datetime.utcnow(),
                }
            },
        )
        raise


# ──────────────────────────────────────────────────────────────────────────
# Phase 2 — Company industry resolution via OpenAI
# ──────────────────────────────────────────────────────────────────────────

async def _run_phase2(
    *,
    run_oid: ObjectId,
    target_industries: List[str],
    jobs_col,
    companies_col,
    runs_col,
) -> Dict[str, int]:
    stats = {"uniqueCompanies": 0, "acceptedCompanies": 0, "rejectedCompanies": 0, "skippedCompanies": 0}

    if not settings.OPENAI_API_KEY:
        print("[Phase2] OPENAI_API_KEY not set — skipping Phase 2")
        return stats
    if not target_industries:
        print("[Phase2] No target industries provided — skipping Phase 2")
        return stats

    try:
        svc = OpenAICompanyService(api_key=settings.OPENAI_API_KEY)
    except Exception as e:
        logger.error("[Phase2] Failed to init OpenAI client: %s", e)
        return stats

    # Authenticate LinkedIn once (reuses cached session). LinkedIn is the source of
    # truth for the company domain — Apollo's people search (Phase 3) filters by exact
    # organization domain, so a GPT-guessed domain matched only ~10% of the time while
    # the real LinkedIn domain matches ~90%. If LinkedIn is unavailable (captcha / no
    # creds) we degrade gracefully to the OpenAI-guessed domain.
    try:
        linkedin_svc = get_linkedin_service()
    except Exception as e:
        logger.error("[Phase2] LinkedIn session unavailable (%s) — falling back to OpenAI domains", e)
        linkedin_svc = None

    # Group accepted jobs by their LinkedIn companyUrl
    cursor = jobs_col.find(
        {"runId": run_oid, "qualityStatus": "good"},
        {"_id": 1, "jobDetails.companyUrl": 1, "company": 1},
    )
    url_to_job_ids: Dict[str, list] = {}
    name_lookup: Dict[str, str] = {}
    async for j in cursor:
        url = (j.get("jobDetails") or {}).get("companyUrl") or ""
        if not url:
            continue
        url_to_job_ids.setdefault(url, []).append(j["_id"])
        name_lookup[url] = j.get("company") or name_lookup.get(url, "")

    stats["uniqueCompanies"] = len(url_to_job_ids)
    print(f"[Phase2] {len(url_to_job_ids)} unique company URLs to resolve")

    for url, job_ids in url_to_job_ids.items():
        slug = LinkedInCompanyService.get_slug(url)

        # ── Authoritative company data from LinkedIn (industry, domain, size, etc.) ──
        # We rely entirely on LinkedIn's reported industry — no LLM company "recall"
        # from the URL, and no hardcoded industry keyword lists.
        li = None
        if linkedin_svc is not None:
            try:
                li = linkedin_svc.fetch_company_info(url)
            except Exception as e:
                logger.warning("[Phase2] LinkedIn lookup failed for %s: %s", url, e)
                li = None

        if not li:
            # Without LinkedIn data we can't determine the industry, so we can't judge
            # the company. Skip it (leave its jobs untouched) rather than mass-rejecting
            # on a transient LinkedIn hiccup.
            logger.warning("[Phase2] No LinkedIn data for %s — skipping", url)
            stats["skippedCompanies"] += 1
            continue

        raw_domain = li.get("companyDomain") or ""
        # MongoDB schema requires companyDomain to match ^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$.
        # Some companies on LinkedIn have no website set → domain is empty → schema fails.
        # Use a placeholder so the doc can be stored; Phase 3 already skips
        # companies whose domain ends with .linkedin.local when searching Apollo.
        fallback_slug = slug or (url.rstrip("/").split("/")[-1] or "unknown")
        domain = raw_domain if raw_domain else f"{fallback_slug}.linkedin.local"

        # URL decode and sanitize domain to match the regex: ^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$
        try:
            domain = urllib.parse.unquote(domain)
        except Exception:
            pass
        domain = re.sub(r'[^a-zA-Z0-9.-]', '', domain.strip())
        if not re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', domain):
            domain = "unknown.linkedin.local"
        staff_count = li.get("staffCount") or 0
        website = li.get("website") or ""
        li_industries = li.get("companyIndustries") or []
        li_description = li.get("description") or ""
        staffing_company = bool(li.get("staffingCompany"))
        company_name = li.get("companyName") or name_lookup.get(url) or ""
        company_industry_text = ", ".join([c for c in li_industries if c])
        company_location = li.get("companyLocation") or ""
        headquarter = li.get("headquarter") or {}

        # ── Semantic industry match (dynamic) ────────────────────────────────
        # Ask the LLM whether LinkedIn's industry for this company belongs to the
        # user's UI-selected target industries (from the run config — fully dynamic).
        try:
            matched_industry = svc.match_industry(li_industries, target_industries)
        except Exception as e:
            logger.error("[Phase2] Industry match failed for %s: %s", url, e)
            matched_industry = None

        targeted = (
            bool(matched_industry)
            and staff_count < settings.MAX_STAFF_COUNT
            and not staffing_company
        )

        # Human-readable reason when the company is NOT targeted — used both on the
        # company doc and to mark its jobs as rejected.
        if targeted:
            reject_reason = ""
        elif not li_industries:
            reject_reason = "No industry listed on LinkedIn"
        elif not matched_industry:
            reject_reason = f"Industry '{company_industry_text or 'unknown'}' not in target list"
        elif staff_count >= settings.MAX_STAFF_COUNT:
            reject_reason = f"Company size {staff_count} exceeds {settings.MAX_STAFF_COUNT}"
        elif staffing_company:
            reject_reason = "Company is a staffing/recruitment agency"
        else:
            reject_reason = "Company not targeted"

        # Choose upsert key: linkedinSlug if available, else domain
        upsert_query = {"linkedinSlug": slug} if slug else ({"companyDomain": domain} if domain else None)
        if upsert_query is None:
            stats["skippedCompanies"] += 1
            continue

        payload = {
            "companyName": company_name or slug or "",
            "companyDomain": domain,
            "companyIndustry": company_industry_text,
            "industry": company_industry_text,
            "matchedIndustry": matched_industry,
            "targeted": targeted,
            "staffCount": staff_count,
            "employeeCount": staff_count,
            "website": website,
            "isEligible": targeted,
            "notes": reject_reason,
            "location": company_location,
            "companyDetails": {
                "description": li_description,
                "website": website,
                "industries": li_industries,
                "staffCount": staff_count,
                "staffingCompany": staffing_company,
                "headquarter": headquarter,
                "companyLocation": company_location,
            },
        }
        # Only write linkedinSlug when we actually extracted one — the Companies
        # schema requires it to be a string, so a None would fail validation.
        if slug:
            payload["linkedinSlug"] = slug
        now = datetime.utcnow()

        # Match an existing company by slug OR domain. companyDomain has a UNIQUE
        # index, and two different LinkedIn slugs can map to the same domain (vanity
        # slugs, or a domain already stored from a previous run). Keying the upsert
        # only on linkedinSlug would then try to insert a duplicate domain → E11000
        # and abort the whole run. So we look up both, reuse when found, and guard
        # the insert against races.
        or_clauses = []
        if slug:
            or_clauses.append({"linkedinSlug": slug})
        if domain:
            or_clauses.append({"companyDomain": domain})
        existing = await companies_col.find_one({"$or": or_clauses}) if or_clauses else None

        company_oid = None
        if existing:
            company_oid = existing["_id"]
            # Don't rewrite the unique identity fields on an existing doc — updating
            # companyDomain to a value owned by another company would collide.
            update_fields = {
                k: v for k, v in payload.items()
                if k not in ("companyDomain", "linkedinSlug")
            }
            await companies_col.update_one(
                {"_id": company_oid},
                {"$set": {**update_fields, "updatedAt": now}},
            )
        else:
            try:
                res = await companies_col.insert_one(
                    {**payload, "createdAt": now, "updatedAt": now}
                )
                company_oid = res.inserted_id
            except DuplicateKeyError:
                # Domain was inserted concurrently / exists under another slug — reuse it.
                dup = await companies_col.find_one({"companyDomain": domain}, {"_id": 1})
                company_oid = dup["_id"] if dup else None

        # Link jobs to the company. If the company was NOT targeted, also flip its
        # jobs to rejected ("poor") with the company-level reason — otherwise jobs of
        # rejected companies stay in the Accepted list even though no prospects are
        # sourced for them.
        if company_oid:
            job_update = {
                "companyId": company_oid,
                "industry": company_industry_text,
                "updatedAt": datetime.utcnow(),
            }
            if not targeted:
                job_update["qualityStatus"] = "poor"
                job_update["rejectionReason"] = f"Company rejected: {reject_reason}"
            await jobs_col.update_many(
                {"_id": {"$in": job_ids}},
                {"$set": job_update},
            )

        if targeted:
            stats["acceptedCompanies"] += 1
        else:
            stats["rejectedCompanies"] += 1

    await runs_col.update_one(
        {"_id": run_oid},
        {"$set": {
            "stats.uniqueCompanies": stats["uniqueCompanies"],
            "stats.acceptedCompanies": stats["acceptedCompanies"],
            "stats.rejectedCompanies": stats["rejectedCompanies"],
            "stats.skippedCompanies": stats["skippedCompanies"],
            "currentPhase": "prospects",
            "updatedAt": datetime.utcnow(),
        }},
    )
    return stats


# ──────────────────────────────────────────────────────────────────────────
# Phase 3 — Apollo prospect search
# ──────────────────────────────────────────────────────────────────────────

async def _run_phase3(
    *,
    run_oid: ObjectId,
    jobs_col,
    companies_col,
    prospects_col,
    runs_col,
) -> Dict[str, int]:
    stats = {"totalProspects": 0, "companiesProcessed": 0}

    if not settings.APOLLO_API_KEY:
        print("[Phase3] APOLLO_API_KEY not set — skipping Phase 3")
        return stats

    apollo = ApolloService()

    # Scope to companies linked to THIS run's accepted jobs (Phase 2 set companyId).
    # Without this, every targeted company from ALL past runs is re-searched, which
    # wastes Apollo credits and triggers 429 rate-limiting.
    company_ids = await jobs_col.distinct(
        "companyId", {"runId": run_oid, "qualityStatus": "good"}
    )
    company_ids = [cid for cid in company_ids if cid]

    targeted = []
    if company_ids:
        cursor = companies_col.find(
            {"_id": {"$in": company_ids}, "targeted": True},
            {"_id": 1, "companyDomain": 1, "matchedIndustry": 1, "companyName": 1},
        )
        async for c in cursor:
            if c.get("companyDomain"):
                targeted.append(c)

    print(f"[Phase3] {len(targeted)} targeted companies for Apollo search (this run)")

    for c in targeted:
        domain = c["companyDomain"]
        industry_name = c.get("matchedIndustry") or ""
        try:
            result = apollo.find_prospects(domain, industry_name, enrich=False)
        except Exception as e:
            logger.error("[Phase3] Apollo failed for %s: %s", domain, e)
            continue
        # Small spacing between companies to stay under Apollo's rate limit.
        await asyncio.sleep(0.5)

        accepted = result.get("accepted", [])
        rejected = result.get("rejected", [])
        stats["companiesProcessed"] += 1

        for p in accepted + rejected:
            is_accepted = p in accepted
            doc = _build_prospect_doc(
                p, run_oid=run_oid, company_oid=c["_id"],
                industry_name=industry_name, is_accepted=is_accepted,
            )
            if not doc:
                continue
            await prospects_col.update_one(
                {"runId": run_oid, "companyId": c["_id"], "apolloId": doc["apolloId"]},
                {"$set": doc, "$setOnInsert": {"createdAt": datetime.utcnow()}},
                upsert=True,
            )
            if is_accepted:
                stats["totalProspects"] += 1

    await runs_col.update_one(
        {"_id": run_oid},
        {"$set": {"stats.totalProspects": stats["totalProspects"], "updatedAt": datetime.utcnow()}},
    )
    return stats


def _build_prospect_doc(p: dict, *, run_oid, company_oid, industry_name: str, is_accepted: bool) -> dict | None:
    apollo_id = p.get("id") or ""
    if not apollo_id:
        return None
    name = (p.get("name") or "").strip()
    first = (p.get("first_name") or (name.split(" ")[0] if name else "")).strip() or "Unknown"
    last_parts = (p.get("last_name") or " ".join(name.split(" ")[1:])).strip()
    last = last_parts or "—"
    email = (p.get("email") or "").strip()
    return {
        "runId": run_oid,
        "companyId": company_oid,
        "apolloId": apollo_id,
        "firstName": first,
        "lastName": last,
        "email": email,
        "title": p.get("title") or "",
        "seniority": p.get("seniority") or "",
        "industryName": industry_name,
        "isEnriched": False,
        "isAccepted": is_accepted,
        "matchReasons": list(p.get("_match_reasons") or []),
        "rejectionReason": p.get("_rejection_reason"),
        "prospectDetails": {
            "linkedinUrl": p.get("linkedin_url") or "",
            "phone": (p.get("phone_numbers") or [{}])[0].get("raw_number") if p.get("phone_numbers") else "",
            "location": p.get("city") or p.get("state") or p.get("country") or "",
        },
        "updatedAt": datetime.utcnow(),
    }
