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

# The set of countries the gate is allowed to be CONFIDENT about. A country_mismatch
# reject may fire ONLY when both sides resolve to a member of this set. Anything
# outside it resolves to None → "unknown" → kept. This is the invariant the gate
# claims but previously broke: an unrecognised location string (e.g. the LinkedIn
# metro label "Frankfurt Rhine-Main Metropolitan Area", which has no comma) used to
# be treated as its own "country" and mismatched against "germany", silently
# dropping in-country candidates — the exact false-negative this gate exists to
# prevent. Now such a string resolves to germany (via the gazetteer) or, if truly
# unrecognised, to unknown (kept). It can never again masquerade as a foreign country.
_KNOWN_COUNTRIES = {
    "germany", "austria", "switzerland", "united states", "united kingdom",
    "france", "netherlands", "spain", "italy", "poland", "india", "ireland",
    "belgium", "portugal", "sweden", "denmark", "norway", "finland", "czechia",
    "czech republic", "hungary", "romania", "greece", "luxembourg", "canada",
    "australia",
}

# Place → country gazetteer. Lets a location that names only a city / state /
# region / metropolitan area (very common on LinkedIn: "Frankfurt Rhine-Main
# Metropolitan Area", "Munich Area", "Greater Zurich Area") resolve POSITIVELY to
# its country instead of degrading to "unknown". DACH-heavy because that is the
# target market; a spread of EU/India anchors keeps genuine wrong-country rejects
# (the Bavaria→India leak) firing even when the string omits the country word.
# Keys are matched as whole-word contiguous sequences, so "rhine main" matches
# inside "frankfurt rhine-main metropolitan area" but "rome" never matches "jerome".
_PLACE_TO_COUNTRY = {
    # Germany — cities
    "berlin": "germany", "munich": "germany", "münchen": "germany", "muenchen": "germany",
    "hamburg": "germany", "frankfurt": "germany", "frankfurt am main": "germany",
    "cologne": "germany", "köln": "germany", "koeln": "germany", "stuttgart": "germany",
    "düsseldorf": "germany", "dusseldorf": "germany", "duesseldorf": "germany",
    "dortmund": "germany", "essen": "germany", "leipzig": "germany", "dresden": "germany",
    "hannover": "germany", "hanover": "germany", "nuremberg": "germany", "nürnberg": "germany",
    "nuernberg": "germany", "bremen": "germany", "bonn": "germany", "mannheim": "germany",
    "karlsruhe": "germany", "wiesbaden": "germany", "münster": "germany", "muenster": "germany",
    "mainz": "germany", "augsburg": "germany", "freiburg": "germany", "heidelberg": "germany",
    "darmstadt": "germany", "duisburg": "germany", "wuppertal": "germany", "bielefeld": "germany",
    "bochum": "germany", "kiel": "germany", "aachen": "germany", "braunschweig": "germany",
    "kassel": "germany", "potsdam": "germany", "regensburg": "germany", "ingolstadt": "germany",
    # Germany — states
    "bavaria": "germany", "bayern": "germany", "hesse": "germany", "hessen": "germany",
    "saxony": "germany", "sachsen": "germany", "baden-württemberg": "germany",
    "baden-wurttemberg": "germany", "baden württemberg": "germany",
    "north rhine-westphalia": "germany", "nordrhein-westfalen": "germany",
    "lower saxony": "germany", "niedersachsen": "germany",
    "rhineland-palatinate": "germany", "rheinland-pfalz": "germany",
    "brandenburg": "germany", "thuringia": "germany", "thüringen": "germany",
    "schleswig-holstein": "germany", "saarland": "germany",
    "mecklenburg-vorpommern": "germany", "mecklenburg": "germany",
    # Germany — regions / metros
    "rhine-main": "germany", "rhein-main": "germany", "rhine main": "germany",
    "rhein main": "germany", "ruhr": "germany", "ruhrgebiet": "germany",
    "rhineland": "germany", "rheinland": "germany",
    # Austria
    "vienna": "austria", "wien": "austria", "graz": "austria", "linz": "austria",
    "salzburg": "austria", "innsbruck": "austria", "klagenfurt": "austria",
    # Switzerland
    "zurich": "switzerland", "zürich": "switzerland", "zuerich": "switzerland",
    "geneva": "switzerland", "genf": "switzerland", "genève": "switzerland",
    "basel": "switzerland", "bern": "switzerland", "lausanne": "switzerland", "zug": "switzerland",
    # A spread of EU anchors — correct POSITIVE rejects against a DACH search.
    "paris": "france", "lyon": "france", "marseille": "france", "toulouse": "france",
    "london": "united kingdom", "manchester": "united kingdom", "birmingham": "united kingdom",
    "madrid": "spain", "barcelona": "spain", "amsterdam": "netherlands", "rotterdam": "netherlands",
    "milan": "italy", "milano": "italy", "rome": "italy", "roma": "italy",
    "brussels": "belgium", "warsaw": "poland", "warszawa": "poland", "dublin": "ireland",
    # India anchors — keep the Bavaria→India leak shut even when "India" is absent.
    "bengaluru": "india", "bangalore": "india", "mumbai": "india", "delhi": "india",
    "new delhi": "india", "hyderabad": "india", "pune": "india", "chennai": "india",
    "kolkata": "india", "noida": "india", "gurgaon": "india", "gurugram": "india",
    "ahmedabad": "india",
}

import re as _re

_WORD_RE = _re.compile(r"[^0-9a-zäöüßéèêëàâçîïôûùüñ]+", _re.UNICODE)


def _words(s: Optional[str]) -> list:
    """Lowercase whole words of a location string ('rhine-main' → ['rhine','main'])."""
    return [w for w in _WORD_RE.split((s or "").lower()) if w]


# Build the gazetteer as (key_word_sequence, country), longest key first.
_GAZETTEER = sorted(
    ((_words(k), c) for k, c in _PLACE_TO_COUNTRY.items()),
    key=lambda kv: -len(kv[0]),
)


def _contains_sequence(hay: list, needle: list) -> bool:
    """True if `needle` appears as a contiguous whole-word run in `hay`."""
    n = len(needle)
    if not n:
        return False
    for i in range(len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return True
    return False


def _resolve_country(location: Optional[str]) -> Optional[str]:
    """Best-effort country for a location string — or None if not CONFIDENTLY known.

    Two stages, both conservative:
      1. Clean "…, Country" — take the last comma segment, alias it, accept only
         if it is a recognised country.
      2. Gazetteer — scan the whole string for a known city/state/region/metro.
    Returns None when neither is conclusive, so the gate keeps (never rejects) on
    an ambiguous location instead of inventing a mismatch.
    """
    if not location:
        return None
    last = extract_country(location)
    if last:
        c = _COUNTRY_ALIASES.get(last.strip(".").strip(), last.strip(".").strip())
        if c in _KNOWN_COUNTRIES:
            return c
    words = _words(location)
    for key_words, country in _GAZETTEER:
        if _contains_sequence(words, key_words):
            return country
    # Stage 3: the shared offline catalogue (diacritic-folded, whole-word — but
    # NOT fuzzy, so it can never manufacture a wrong-country reject). Recognises
    # the long tail of DACH cities (Koblenz, Trier, Kaiserslautern…) the inline
    # gazetteer above omits, so a bare-city requested location still gates.
    from app.services import location_catalog
    cat = location_catalog.country_of(location, fuzzy=False)
    if cat and cat.lower() in _KNOWN_COUNTRIES:
        return cat.lower()
    return None


def canonical_country(name: Optional[str]) -> Optional[str]:
    """Public: the confidently-resolved country of a location, else None.

    Backwards-compatible name; now backed by the alias table AND the place
    gazetteer, and — critically — returns None (not a raw string) for anything it
    cannot resolve to a KNOWN country, so callers can never treat an unresolved
    location as a distinct 'country'."""
    return _resolve_country(name)


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
        # Absent OR not confidently resolvable to a known country on one side.
        # Either way the gate must NOT reject — an unrecognised location is not
        # evidence of a wrong one. This is the invariant whose breach silently
        # dropped in-country candidates whose location was a bare metro label.
        return {"decision": "unknown",
                "reason": "Location not conclusively resolved on one side — kept.",
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
