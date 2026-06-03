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
