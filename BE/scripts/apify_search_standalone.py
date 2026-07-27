"""
===============================================================================
Apify LinkedIn Profile Search — Standalone Script
Actor: harvestapi/linkedin-profile-search (ID: M2FMdjRVeF1HPGFcc)
===============================================================================

This script demonstrates how to query the HarvestAPI LinkedIn Profile Search actor
on Apify using all available filter parameters, including:
  • Fuzzy Search Query & Job Titles (Current & Past)
  • Locations & Headquarter Locations (Include & Exclude)
  • Companies & Schools (Include & Exclude)
  • Industry IDs (Include & Exclude - linked to LinkedIn Industry Codes v2)
  • Years of Experience & Years at Current Company
  • Seniority Level & Function Filters (Include & Exclude)
  • Company Headcount Ranges
  • Profile Languages, Recently Changed Jobs, Recently Posted on LinkedIn
  • Automatic Query Segmentation (Levels & Target Countries)
  • Profile Scraper Modes ("Short", "Full", "Full + email search")

Usage:
  1. Set your APIFY_TOKEN in .env or pass it as an argument / env variable.
  2. Run: python BE/scripts/apify_search_standalone.py
===============================================================================
"""

import os
import sys
import json
import logging
from typing import List, Dict, Any, Optional

# Load .env if python-dotenv is installed
try:
    from dotenv import load_dotenv
    # Look for .env in BE directory or current working directory
    be_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(be_dir, ".env"))
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ApifyStandalone")


# ===============================================================================
# Filter Code Reference Tables (from HarvestAPI documentation)
# ===============================================================================

# 1. Years of Experience & Years at Current Company IDs
YEARS_FILTER_CODES = {
    "1": "Less than 1 year",
    "2": "1 to 2 years",
    "3": "3 to 5 years",
    "4": "6 to 10 years",
    "5": "More than 10 years",
}

# 2. Seniority Level IDs
SENIORITY_LEVEL_CODES = {
    "100": "In Training",
    "110": "Entry Level",
    "120": "Senior",
    "130": "Strategic",
    "200": "Entry Level Manager",
    "210": "Experienced Manager",
    "220": "Director",
    "300": "Vice President",
    "310": "CXO",
    "320": "Owner / Partner",
}

# 3. Function Filter IDs
FUNCTION_CODES = {
    "1": "Accounting",
    "2": "Administrative",
    "3": "Arts and Design",
    "4": "Business Development",
    "5": "Community and Social Services",
    "6": "Consulting",
    "7": "Education",
    "8": "Engineering",
    "9": "Entrepreneurship",
    "10": "Finance",
    "11": "Healthcare Services",
    "12": "Human Resources",
    "13": "Information Technology",
    "14": "Legal",
    "15": "Marketing",
    "16": "Media and Communication",
    "17": "Military and Protective Services",
    "18": "Operations",
    "19": "Product Management",
    "20": "Program and Project Management",
    "21": "Purchasing",
    "22": "Quality Assurance",
    "23": "Real Estate",
    "24": "Research",
    "25": "Sales",
    "26": "Customer Success and Support",
}

# 4. Company Headcount Range Codes
COMPANY_HEADCOUNT_CODES = {
    "A": "Self-Employed",
    "B": "1-10",
    "C": "11-50",
    "D": "51-200",
    "E": "201-500",
    "F": "501-1,000",
    "G": "1,001-5,000",
    "H": "5,001-10,000",
    "I": "10,001+",
}

# Common Industry ID examples (Reference: https://github.com/HarvestAPI/linkedin-industry-codes-v2)
POPULAR_INDUSTRY_IDS = {
    "4": "Software Development",
    "6": "Technology, Information and Internet",
    "96": "IT Services and IT Consulting",
    "43": "Financial Services",
    "14": "Hospitals and Health Care",
    "80": "Business Consulting and Services",
}


# Helper reverse maps for resolving human names to codes
def _build_reverse_lookup(code_table: Dict[str, str]) -> Dict[str, str]:
    lookup = {}
    for code, label in code_table.items():
        lookup[code.lower()] = code
        lookup[label.lower().replace(" ", "").replace("-", "").replace(",", "")] = code
    return lookup


_REVERSE_YEARS = _build_reverse_lookup(YEARS_FILTER_CODES)
_REVERSE_SENIORITY = _build_reverse_lookup(SENIORITY_LEVEL_CODES)
_REVERSE_FUNCTION = _build_reverse_lookup(FUNCTION_CODES)
_REVERSE_HEADCOUNT = _build_reverse_lookup(COMPANY_HEADCOUNT_CODES)


def resolve_codes(values: Optional[List[str]], reverse_lookup: Dict[str, str]) -> List[str]:
    """Helper to convert human titles or raw string codes into valid actor code strings."""
    if not values:
        return []
    resolved = []
    for val in values:
        if not val:
            continue
        val_str = str(val).strip()
        norm_key = val_str.lower().replace(" ", "").replace("-", "").replace(",", "")
        code = reverse_lookup.get(norm_key) or reverse_lookup.get(val_str.lower())
        if code and code not in resolved:
            resolved.append(code)
        elif not code:
            # If not in lookup, pass raw string if it looks like a valid code
            resolved.append(val_str)
    return resolved


# ===============================================================================
# Input Builder
# ===============================================================================

def build_apify_search_payload(
    search_query: Optional[str] = None,
    locations: Optional[List[str]] = None,
    exclude_locations: Optional[List[str]] = None,
    current_job_titles: Optional[List[str]] = None,
    exclude_current_job_titles: Optional[List[str]] = None,
    past_job_titles: Optional[List[str]] = None,
    exclude_past_job_titles: Optional[List[str]] = None,
    current_companies: Optional[List[str]] = None,
    exclude_current_companies: Optional[List[str]] = None,
    past_companies: Optional[List[str]] = None,
    exclude_past_companies: Optional[List[str]] = None,
    schools: Optional[List[str]] = None,
    exclude_schools: Optional[List[str]] = None,
    industry_ids: Optional[List[str]] = None,
    exclude_industry_ids: Optional[List[str]] = None,
    years_of_experience_ids: Optional[List[str]] = None,
    years_at_current_company_ids: Optional[List[str]] = None,
    seniority_level_ids: Optional[List[str]] = None,
    exclude_seniority_level_ids: Optional[List[str]] = None,
    function_ids: Optional[List[str]] = None,
    exclude_function_ids: Optional[List[str]] = None,
    company_headcount: Optional[List[str]] = None,
    company_hq_locations: Optional[List[str]] = None,
    exclude_company_hq_locations: Optional[List[str]] = None,
    first_names: Optional[List[str]] = None,
    last_names: Optional[List[str]] = None,
    profile_languages: Optional[List[str]] = None,
    recently_changed_jobs: Optional[bool] = None,
    recently_posted_on_linkedin: Optional[bool] = None,
    profile_scraper_mode: str = "Short",  # "Short", "Full", or "Full + email search"
    max_items: int = 25,
    start_page: int = 1,
    take_pages: Optional[int] = None,
    auto_query_segmentation: Optional[bool] = None,
    auto_query_segmentation_levels: Optional[List[str]] = None,  # e.g., ["default", "country", "seniority_level"]
    auto_query_segmentation_target_countries: Optional[List[str]] = None,  # e.g., ["US", "DE", "IN"]
) -> Dict[str, Any]:
    """Construct the exact JSON payload expected by harvestapi/linkedin-profile-search."""

    payload: Dict[str, Any] = {
        "profileScraperMode": profile_scraper_mode,
        "maxItems": max_items,
        "startPage": start_page,
    }

    if search_query:
        payload["searchQuery"] = search_query.strip()
    if take_pages:
        payload["takePages"] = take_pages

    # List string filters
    if locations:
        payload["locations"] = locations
    if exclude_locations:
        payload["excludeLocations"] = exclude_locations
    if current_job_titles:
        payload["currentJobTitles"] = current_job_titles
    if exclude_current_job_titles:
        payload["excludeCurrentJobTitles"] = exclude_current_job_titles
    if past_job_titles:
        payload["pastJobTitles"] = past_job_titles
    if exclude_past_job_titles:
        payload["excludePastJobTitles"] = exclude_past_job_titles
    if current_companies:
        payload["currentCompanies"] = current_companies
    if exclude_current_companies:
        payload["excludeCurrentCompanies"] = exclude_current_companies
    if past_companies:
        payload["pastCompanies"] = past_companies
    if exclude_past_companies:
        payload["excludePastCompanies"] = exclude_past_companies
    if schools:
        payload["schools"] = schools
    if exclude_schools:
        payload["excludeSchools"] = exclude_schools
    if industry_ids:
        payload["industryIds"] = [str(x) for x in industry_ids]
    if exclude_industry_ids:
        payload["excludeIndustryIds"] = [str(x) for x in exclude_industry_ids]
    if company_hq_locations:
        payload["companyHeadquarterLocations"] = company_hq_locations
    if exclude_company_hq_locations:
        payload["excludeCompanyHeadquarterLocations"] = exclude_company_hq_locations
    if first_names:
        payload["firstNames"] = first_names
    if last_names:
        payload["lastNames"] = last_names
    if profile_languages:
        payload["profileLanguages"] = profile_languages

    # Enum Code Filters (resolved to string codes)
    if years_of_experience_ids:
        payload["yearsOfExperienceIds"] = resolve_codes(years_of_experience_ids, _REVERSE_YEARS)
    if years_at_current_company_ids:
        payload["yearsAtCurrentCompanyIds"] = resolve_codes(years_at_current_company_ids, _REVERSE_YEARS)
    if seniority_level_ids:
        payload["seniorityLevelIds"] = resolve_codes(seniority_level_ids, _REVERSE_SENIORITY)
    if exclude_seniority_level_ids:
        payload["excludeSeniorityLevelIds"] = resolve_codes(exclude_seniority_level_ids, _REVERSE_SENIORITY)
    if function_ids:
        payload["functionIds"] = resolve_codes(function_ids, _REVERSE_FUNCTION)
    if exclude_function_ids:
        payload["excludeFunctionIds"] = resolve_codes(exclude_function_ids, _REVERSE_FUNCTION)
    if company_headcount:
        payload["companyHeadcount"] = resolve_codes(company_headcount, _REVERSE_HEADCOUNT)

    # Boolean filters
    if recently_changed_jobs is not None:
        payload["recentlyChangedJobs"] = bool(recently_changed_jobs)
    if recently_posted_on_linkedin is not None:
        payload["recentlyPostedOnLinkedIn"] = bool(recently_posted_on_linkedin)

    # Segmentation options
    if auto_query_segmentation is not None:
        payload["autoQuerySegmentation"] = bool(auto_query_segmentation)
    elif max_items > 25 and (search_query or current_job_titles):
        payload["autoQuerySegmentation"] = True

    if auto_query_segmentation_levels:
        payload["autoQuerySegmentationLevels"] = auto_query_segmentation_levels
    if auto_query_segmentation_target_countries:
        payload["autoQuerySegmentationTargetCountries"] = auto_query_segmentation_target_countries

    return payload


# ===============================================================================
# Execution Engine (Apify Client SDK with Requests fallback)
# ===============================================================================

def run_apify_search(payload: Dict[str, Any], token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Execute the HarvestAPI actor on Apify and return extracted dataset items."""

    api_token = token or os.getenv("APIFY_TOKEN")
    if not api_token:
        logger.error("APIFY_TOKEN environment variable is not set!")
        logger.info("Printing constructed JSON payload for inspection instead of running live API call:")
        print(json.dumps(payload, indent=2))
        return []

    actor_id = os.getenv("APIFY_SEARCH_ACTOR", "harvestapi/linkedin-profile-search")
    logger.info("Calling Apify actor '%s' with maxItems=%s...", actor_id, payload.get("maxItems"))

    # Strategy A: Try using official `apify_client` SDK if installed
    try:
        from apify_client import ApifyClient
        client = ApifyClient(api_token)
        logger.info("Executing via apify-client SDK...")
        run = client.actor(actor_id).call(run_input=payload)
        
        status = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
        dataset_id = run.get("defaultDatasetId") if isinstance(run, dict) else getattr(run, "default_dataset_id", None)
        logger.info("Actor run finished with status: %s | datasetId: %s", status, dataset_id)

        if not dataset_id:
            logger.error("Run completed without datasetId")
            return []

        items = list(client.dataset(dataset_id).iterate_items())
        logger.info("Successfully fetched %d items from dataset.", len(items))
        return items

    except ImportError:
        logger.info("apify-client SDK not found, falling back to direct HTTP API via requests...")

    # Strategy B: Fallback to direct HTTP REST API via `requests`
    import requests
    url = f"https://api.apify.com/v2/acts/harvestapi~linkedin-profile-search/run-sync-get-dataset-items?token={api_token}"
    headers = {"Content-Type": "application/json"}
    
    logger.info("Sending POST request to Apify REST API...")
    res = requests.post(url, headers=headers, json=payload, timeout=300)
    
    if res.status_code != 200:
        logger.error("Apify API HTTP %d error: %s", res.status_code, res.text)
        return []

    items = res.json()
    logger.info("Successfully fetched %d items via REST API.", len(items))
    return items


# ===============================================================================
# Main Demonstration
# ===============================================================================

if __name__ == "__main__":
    logger.info("=== Apify LinkedIn Profile Search Standalone Test ===")

    # Example 1: Search for Senior Python Developers / Tech Leads in Germany or India
    # with 3 to 10+ years experience, Seniority: Senior / Strategic / Manager
    payload_ex1 = build_apify_search_payload(
        search_query="Python Backend Developer",
        current_job_titles=["Senior Python Developer", "Lead Backend Engineer"],
        locations=["Germany", "India"],
        years_of_experience_ids=["3", "4", "5"],  # "3 to 5 years", "6 to 10 years", "More than 10 years"
        seniority_level_ids=["120", "210", "220"], # "Senior", "Experienced Manager", "Director"
        function_ids=["8", "13"],                 # "Engineering", "Information Technology"
        company_headcount=["D", "E", "F"],        # "51-200", "201-500", "501-1000"
        recently_changed_jobs=True,
        profile_scraper_mode="Short",
        max_items=10,
    )

    print("\n--- Constructed Search Input Payload (Example 1) ---")
    print(json.dumps(payload_ex1, indent=2))

    # Run query if token exists
    token = os.getenv("APIFY_TOKEN")
    if token:
        logger.info("\n=== Executing Live Apify Search ===")
        results = run_apify_search(payload_ex1, token=token)
        print(f"\nFetched {len(results)} candidate results:")
        for idx, cand in enumerate(results[:5], 1):
            name = f"{cand.get('firstName', '')} {cand.get('lastName', '')}".strip() or cand.get('name', 'N/A')
            pos = cand.get('currentPosition') or cand.get('currentPositions') or []
            title = pos[0].get('title', '') if isinstance(pos, list) and pos else 'N/A'
            comp = pos[0].get('companyName', '') if isinstance(pos, list) and pos else 'N/A'
            url = cand.get('linkedinUrl', '')
            print(f" {idx}. {name} | {title} at {comp} | {url}")
    else:
        logger.warning("\nNo APIFY_TOKEN set in environment. Set APIFY_TOKEN in BE/.env to test live API calls.")
