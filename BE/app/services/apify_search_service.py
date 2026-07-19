"""
Apify LinkedIn people-SEARCH service (candidate discovery).

Given a filter set (title / location / seniority / function / years-of-experience
/ …) this runs the ``harvestapi/linkedin-profile-search`` actor in Short mode
($0.1 per search page, ≤25 short profiles/page) and returns short profiles —
id, name, current position, location, LinkedIn URL. Those become pipeline
candidates, which are then deep-enriched by the profile scraper.

The actor's input keys mirror the fields shown in its Console form. Only
non-empty filters are sent ("remove empty fields"). If a key ever needs tweaking,
it's the single ``_build_input`` map below — check the actor's JSON tab for the
authoritative names.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.apify_profile_service import (
    ApifyEnrichmentError, ApifyNotConfigured, ApifyRunFailed, _run_to_dict,
    call_actor_bounded,
)

logger = logging.getLogger(__name__)

_COST_PER_PAGE = 0.10  # Short mode — for a cost log line only.


# ──────────────────────────────────────────────────────────────────────────────
# Input mapping (our normalized filters → actor input)
#
# Keys/values below are taken verbatim from the actor's own input schema
# (harvestapi~linkedin-profile-search, builds/default). All list filters use
# PLURAL keys; the "enum" filters are ARRAYS of code strings (not human labels),
# so we translate the labels the UI shows → the codes the actor requires.
# ──────────────────────────────────────────────────────────────────────────────

# our filter key → actor input key. Both are plural string-lists.
_LIST_FILTERS = {
    "locations": "locations",
    "currentJobTitles": "currentJobTitles",
    "pastJobTitles": "pastJobTitles",
    "currentCompanies": "currentCompanies",
    "pastCompanies": "pastCompanies",
    "schools": "schools",
    "industryIds": "industryIds",
    "firstNames": "firstNames",
    "lastNames": "lastNames",
    "companyHqLocations": "companyHeadquarterLocations",
    "excludeLocations": "excludeLocations",
    "excludeCurrentCompanies": "excludeCurrentCompanies",
    "excludePastCompanies": "excludePastCompanies",
    "excludeSchools": "excludeSchools",
    "excludeCurrentJobTitles": "excludeCurrentJobTitles",
    "excludePastJobTitles": "excludePastJobTitles",
    "excludeIndustryIds": "excludeIndustryIds",
    "excludeCompanyHqLocations": "excludeCompanyHeadquarterLocations",
}

# Enum tables: code → human title (from the schema's enum/enumTitles). We build a
# reverse lookup that accepts either the code or the title (case/space/comma
# -insensitive) so a value from the UI resolves to the code the actor wants.
_YEARS = {"1": "Less than 1 year", "2": "1 to 2 years", "3": "3 to 5 years",
          "4": "6 to 10 years", "5": "More than 10 years"}
_SENIORITY = {"100": "In Training", "110": "Entry Level", "120": "Senior",
              "130": "Strategic", "200": "Entry Level Manager",
              "210": "Experienced Manager", "220": "Director",
              "300": "Vice President", "310": "CXO", "320": "Owner / Partner"}
_FUNCTION = {"1": "Accounting", "2": "Administrative", "3": "Arts and Design",
             "4": "Business Development", "5": "Community and Social Services",
             "6": "Consulting", "7": "Education", "8": "Engineering",
             "9": "Entrepreneurship", "10": "Finance", "11": "Healthcare Services",
             "12": "Human Resources", "13": "Information Technology", "14": "Legal",
             "15": "Marketing", "16": "Media and Communication",
             "17": "Military and Protective Services", "18": "Operations",
             "19": "Product Management", "20": "Program and Project Management",
             "21": "Purchasing", "22": "Quality Assurance", "23": "Real Estate",
             "24": "Research", "25": "Sales", "26": "Customer Success and Support"}
_HEADCOUNT = {"A": "Self-Employed", "B": "1-10", "C": "11-50", "D": "51-200",
              "E": "201-500", "F": "501-1,000", "G": "1,001-5,000",
              "H": "5,001-10,000", "I": "10,001+"}
_LANGUAGES = {s.lower(): s for s in (
    "Arabic English Spanish Portuguese Chinese French Italian Russian German "
    "Dutch Turkish Tagalog Polish Korean Japanese Malay Norwegian Danish "
    "Romanian Swedish".split() + ["Bahasa Indonesia", "Czech"])}


def _norm(s: Any) -> str:
    return "".join(ch for ch in str(s).strip().lower() if ch not in " ,")


def _reverse(table: Dict[str, str]) -> Dict[str, str]:
    """{code: title} → {normalized(code or title): code}."""
    out: Dict[str, str] = {}
    for code, title in table.items():
        out[_norm(code)] = code
        out[_norm(title)] = code
    return out


# our filter key → (actor key, code-resolver). Actor expects an ARRAY of codes.
_ENUM_FILTERS = {
    "yearsOfExperience": ("yearsOfExperienceIds", _reverse(_YEARS)),
    "yearsAtCurrentCompany": ("yearsAtCurrentCompanyIds", _reverse(_YEARS)),
    "seniorityLevel": ("seniorityLevelIds", _reverse(_SENIORITY)),
    "excludeSeniorityLevel": ("excludeSeniorityLevelIds", _reverse(_SENIORITY)),
    "function": ("functionIds", _reverse(_FUNCTION)),
    "excludeFunction": ("excludeFunctionIds", _reverse(_FUNCTION)),
    "companyHeadcount": ("companyHeadcount", _reverse(_HEADCOUNT)),
    "profileLanguages": ("profileLanguages", {_norm(v): v for v in _LANGUAGES.values()}),
}


# Public enum vocabulary: {our filter key: {code: human title}}. The sourcing
# agents are prompted on these tables so an LLM can only ever propose a value the
# actor accepts, and the UI can render the same labels. Read-only — mutate the
# private tables above instead.
ENUM_TABLES: Dict[str, Dict[str, str]] = {
    "yearsOfExperience": _YEARS,
    "yearsAtCurrentCompany": _YEARS,
    "seniorityLevel": _SENIORITY,
    "excludeSeniorityLevel": _SENIORITY,
    "function": _FUNCTION,
    "excludeFunction": _FUNCTION,
    "companyHeadcount": _HEADCOUNT,
}


def resolve_enum(our_key: str, value: Any) -> Optional[str]:
    """Normalize a code-or-human-title to the code the actor wants.

    Accepts either form ("120" or "Senior") case/space/comma-insensitively.
    Returns None for blank or unrecognised values, so callers can drop the
    filter rather than send something the actor will reject.
    """
    entry = _ENUM_FILTERS.get(our_key)
    if not entry or value is None or str(value).strip() == "":
        return None
    return entry[1].get(_norm(value))


def _clean_list(v: Any) -> List[str]:
    if not v:
        return []
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in v if x and str(x).strip()]


# The actor reports account-level refusals as a SUCCEEDED run with an empty
# dataset and an explanatory statusMessage. Without this check that is
# indistinguishable from "nobody matches your search", which is the worst possible
# confusion: the recruiter is told their role has no candidates, the Broadener
# burns its whole retry budget widening a search that was never run, and the
# pipeline silently empties. Observed live: "free user run limit reached" returned
# 0 profiles for every query, including ones that had returned results an hour
# earlier.
_QUOTA_MARKERS = ("run limit reached", "usage limit", "quota", "exceeded",
                  "insufficient credit", "payment required", "upgrade")


def _raise_if_quota_exhausted(info: Dict[str, Any]) -> None:
    msg = str(info.get("statusMessage") or info.get("status_message") or "").strip()
    if not msg:
        return
    low = msg.lower()
    if any(m in low for m in _QUOTA_MARKERS):
        raise ApifyRunFailed(
            f"Apify refused the search: {msg!r}. The run 'succeeded' but returned no "
            f"data — this is an account/billing limit, NOT an empty candidate pool. "
            f"Do not treat it as 'no candidates found'."
        )


def _build_input(filters: Dict[str, Any], max_items: int) -> Dict[str, Any]:
    """Build the actor run input, dropping empty filters."""
    # The questionnaire's singular `profileLanguage` predates the plural actor
    # key; every consumer here reads the plural. Normalize instead of dropping —
    # this field was dead for months because nothing read the singular.
    if filters.get("profileLanguage") and not filters.get("profileLanguages"):
        filters = {**filters, "profileLanguages": [filters["profileLanguage"]]}

    run_input: Dict[str, Any] = {
        "profileScraperMode": settings.APIFY_SEARCH_MODE,
        "maxItems": max_items,
    }
    q = (filters.get("searchQuery") or "").strip()
    if q:
        run_input["searchQuery"] = q
    for our_key, actor_key in _LIST_FILTERS.items():
        vals = _clean_list(filters.get(our_key))
        if vals:
            run_input[actor_key] = vals
    for our_key, (actor_key, resolver) in _ENUM_FILTERS.items():
        raw = filters.get(our_key)
        raw_list = raw if isinstance(raw, list) else [raw]
        codes = []
        for v in raw_list:
            if v is None or str(v).strip() == "":
                continue
            code = resolver.get(_norm(v))
            if code and code not in codes:
                codes.append(code)
            elif code is None:
                logger.warning("[ApifySearch] dropping unknown %s value %r", our_key, v)
        if codes:
            run_input[actor_key] = codes
    if filters.get("recentlyChangedJobs"):
        run_input["recentlyChangedJobs"] = True
    if filters.get("recentlyPostedOnLinkedin"):
        run_input["recentlyPostedOnLinkedIn"] = True
    return run_input


# ──────────────────────────────────────────────────────────────────────────────
# Short-profile parsing (actor item → flat candidate fields)
# ──────────────────────────────────────────────────────────────────────────────

def parse_short_profile(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map a search result item to the flat fields we store on a candidate.
    Returns None for an item with no usable identity."""
    if not isinstance(item, dict):
        return None
    profile_id = item.get("id") or item.get("profileId") or item.get("profileIdInSearch")
    url = item.get("linkedinUrl") or (f"https://www.linkedin.com/in/{profile_id}" if profile_id else "")
    if not profile_id and not url:
        return None

    positions = item.get("currentPositions") or item.get("currentPosition") or []
    cur = positions[0] if isinstance(positions, list) and positions else {}
    loc = item.get("location") or {}
    location = ""
    if isinstance(loc, dict):
        location = loc.get("linkedinText") or ((loc.get("parsed") or {}).get("text") if isinstance(loc.get("parsed"), dict) else "")
    elif isinstance(loc, str):
        location = loc

    first = (item.get("firstName") or "").strip()
    last = (item.get("lastName") or "").strip()
    return {
        "profileId": str(profile_id) if profile_id else url,
        "linkedinUrl": url,
        "firstName": first or "Unknown",
        "lastName": last or "",
        "displayName": f"{first} {last}".strip() or (item.get("name") or "").strip(),
        "currentTitle": (cur.get("title") or "").strip() if isinstance(cur, dict) else "",
        "currentCompany": (cur.get("companyName") or "").strip() if isinstance(cur, dict) else "",
        "location": location or "",
        "photoUrl": item.get("pictureUrl") or item.get("photoUrl") or "",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Service
# ──────────────────────────────────────────────────────────────────────────────

class ApifySearchService:
    def __init__(self, token: Optional[str] = None, actor: Optional[str] = None) -> None:
        self._token = token or settings.APIFY_TOKEN
        self._actor = actor or settings.APIFY_SEARCH_ACTOR

    def _client(self):
        if not self._token:
            raise ApifyNotConfigured("APIFY_TOKEN is not set — add it to BE/.env to search.")
        try:
            from apify_client import ApifyClient
        except ImportError as exc:  # pragma: no cover
            raise ApifyEnrichmentError("apify-client is not installed — `pip install apify-client`.") from exc
        return ApifyClient(self._token)

    def search(self, filters: Dict[str, Any], *, max_items: int = 25) -> List[Dict[str, Any]]:
        """Run the search actor and return raw short-profile items."""
        max_items = max(1, min(int(max_items or 25), 100))
        run_input = _build_input(filters, max_items)
        logger.info("[ApifySearch] %s · maxItems=%d · keys=%s (~$%.2f)",
                    self._actor, max_items, sorted(run_input.keys()), _COST_PER_PAGE)

        client = self._client()
        try:
            # Bounded run + client wait so a hung actor can't leave the job stuck
            # on "running" forever. Version-tolerant kwarg names — see
            # apify_profile_service.call_actor_bounded.
            run = call_actor_bounded(
                client, self._actor, run_input,
                timeout_secs=settings.APIFY_CALL_TIMEOUT_SECS,
            )
        except Exception as exc:
            raise ApifyRunFailed(f"Apify search actor call failed: {exc}") from exc

        info = _run_to_dict(run)
        if info.get("status") != "SUCCEEDED":
            raise ApifyRunFailed(f"Apify search run status {info.get('status')!r} (expected SUCCEEDED).")
        _raise_if_quota_exhausted(info)
        dataset_id = info.get("defaultDatasetId") or info.get("default_dataset_id")
        if not dataset_id:
            raise ApifyRunFailed("Apify search run returned no defaultDatasetId.")

        items: List[Dict[str, Any]] = []
        for it in client.dataset(dataset_id).iterate_items():
            if isinstance(it, dict) and not (it.get("error") and not it.get("id")):
                items.append(it)
        logger.info("[ApifySearch] got %d profiles", len(items))

        # Meter the search cost (vendor actual if reported, else Short-mode page).
        # Best-effort by design (metering must not fail a paid search that already
        # succeeded) — but LOGGED: a silent pass here hid every gap in the ledger.
        try:
            from app.services import cost_service
            vendor = info.get("usageTotalUsd") or info.get("usage_total_usd")
            cost_service.record_event(
                service="apify", operation="profile_search", unit="page", quantity=1,
                cost_override=(float(vendor) if vendor else _COST_PER_PAGE),
                vendor_ref=str(info.get("id") or dataset_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ApifySearch] cost metering failed (search succeeded): %s", exc)
        return items[:max_items]


_service: Optional[ApifySearchService] = None


def get_apify_search_service() -> ApifySearchService:
    global _service
    if _service is None:
        _service = ApifySearchService()
    return _service
