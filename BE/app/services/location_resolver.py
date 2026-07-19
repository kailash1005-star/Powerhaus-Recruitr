"""
Location resolution for candidate searches.

A job's location string is rich but inconsistent (e.g. "Berlin, Berlin,
Germany", "Hamburg, Germany", "Hesse, Germany", or empty). For candidate
sourcing we only need the COUNTRY — we pass it to Apollo as a free-text
``person_locations[]`` filter. This module provides a single helper to extract
the country from any of our location sources, with a clear priority order.
"""
from __future__ import annotations

from typing import Optional


def extract_country(location: Optional[str]) -> Optional[str]:
    """Return the lowercase country name from a location string.

    Assumes the country is the LAST comma-separated segment. Verified against
    all distinct ``job.location`` values in production data — every entry
    follows "City, [State,] Country" so the rightmost segment is the country.
    Returns None for empty / whitespace-only input.
    """
    if not location:
        return None
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if not parts:
        return None
    return parts[-1].lower()


def resolve_search_country(
    *,
    job_location: Optional[str] = None,
    search_location: Optional[str] = None,
    company_location: Optional[str] = None,
) -> Optional[str]:
    """Pick the country to feed to Apollo's ``person_locations[]`` filter.

    Priority:
      1. job.location (most specific to the role)
      2. jobDetails.searchLocation (run-config fallback — always populated for
         scraped jobs, normally country-level)
      3. company.location (HQ — useful when both job sources are empty)

    Returns None if all three are unavailable; the caller should mark the
    search ``failed`` with ``"no location available"`` in that case.
    """
    for source in (job_location, search_location, company_location):
        country = extract_country(source)
        if country:
            return country
    return None


# ── Deterministic result-location gate (no LLM) ──────────────────────────────
# "Is 'Bengaluru, Karnataka, India' inside 'Bavaria, Germany'?" is an EXACT
# question. A code gate answers it perfectly, for free, and can never
# hallucinate — the wrong tool for this is an LLM, however capable. The gate
# runs over every discovery result before it reaches the recruiter.

# Common country aliases so "Deutschland" ≡ "Germany" ≡ "DE". Deliberately
# small: a missing alias degrades to "unknown" (kept, flagged), never a false
# reject — the gate is only allowed to be confident when it truly is.
_COUNTRY_ALIASES = {
    "deutschland": "germany", "de": "germany", "ger": "germany", "brd": "germany",
    "österreich": "austria", "oesterreich": "austria", "at": "austria",
    "schweiz": "switzerland", "suisse": "switzerland", "ch": "switzerland",
    "usa": "united states", "us": "united states", "u.s.": "united states",
    "u.s.a.": "united states", "america": "united states",
    "united states of america": "united states", "uk": "united kingdom",
    "u.k.": "united kingdom", "great britain": "united kingdom",
    "england": "united kingdom", "nederland": "netherlands", "nl": "netherlands",
    "france": "france", "fr": "france", "españa": "spain", "espana": "spain",
    "italia": "italy", "polska": "poland", "india": "india", "in": "india",
    "bharat": "india",
}


def canonical_country(name: Optional[str]) -> Optional[str]:
    """Lowercased canonical country name, resolving common aliases. None when
    the input carries no country segment."""
    c = extract_country(name)
    if not c:
        return None
    c = c.strip().lower().strip(".")
    return _COUNTRY_ALIASES.get(c, c)


def _region_tokens(location: Optional[str]) -> set:
    """The non-country segments of a location, lowercased (city/state/region).

    A country-only string ("Germany") has NO region — the last segment is always
    the country and is dropped. Returning it as a region would make every
    country-level request spuriously "region-mismatch" against any city.
    """
    if not location:
        return set()
    parts = [p.strip().lower() for p in location.split(",") if p.strip()]
    return set(parts[:-1])


def location_verdict(requested: Optional[str], candidate: Optional[str]) -> dict:
    """Compare a candidate's location against the requested one.

    Returns {"decision", "reason", "requestedCountry", "candidateCountry"} where
    decision is:
      * "match"           — same country (region may or may not align).
      * "region_mismatch" — same country, different region. KEPT + flagged:
                            remote work and relocation make this legitimate, so
                            a hard reject here would be the false-negative crime.
      * "country_mismatch"— different country. The Bavaria→India leak. REJECT.
      * "unknown"         — not enough location text on one side to judge. KEPT:
                            never reject on absent signal.

    Deterministic and side-effect-free — the caller owns what to do with each
    decision (see candidate_pipeline._store_profiles).
    """
    req_c = canonical_country(requested)
    cand_c = canonical_country(candidate)
    if not req_c or not cand_c:
        return {"decision": "unknown", "reason": "Location not stated on one side — kept.",
                "requestedCountry": req_c, "candidateCountry": cand_c}
    if req_c != cand_c:
        return {
            "decision": "country_mismatch",
            "reason": f"Wanted {req_c.title()}; candidate is in {cand_c.title()}.",
            "requestedCountry": req_c, "candidateCountry": cand_c,
        }
    req_r = _region_tokens(requested)
    cand_r = _region_tokens(candidate)
    if req_r and cand_r and not (req_r & cand_r):
        return {
            "decision": "region_mismatch",
            "reason": (f"Same country ({req_c.title()}) but a different region — "
                       f"kept (remote/relocation possible)."),
            "requestedCountry": req_c, "candidateCountry": cand_c,
        }
    return {"decision": "match", "reason": f"Location matches ({req_c.title()}).",
            "requestedCountry": req_c, "candidateCountry": cand_c}


def requested_location(filters: dict, requirements: Optional[dict] = None) -> Optional[str]:
    """The location the recruiter actually asked the SEARCH for.

    Priority: the search filter's explicit locations (what the actor was told to
    return) over the JD's parsed location, because the filter is the recruiter's
    direct instruction while the JD location is inferred.
    """
    locs = (filters or {}).get("locations")
    if isinstance(locs, list) and locs:
        first = next((str(x).strip() for x in locs if str(x or "").strip()), None)
        if first:
            return first
    elif isinstance(locs, str) and locs.strip():
        return locs.strip()
    if requirements and (requirements.get("location") or "").strip():
        return str(requirements["location"]).strip()
    return None
