"""
Candidate merge layer — Apollo record + Apify profile → one enriched candidate.

Apollo and the Apify (HarvestAPI) profile scraper are COMPLEMENTARY, not
competing:

  • Apollo owns   : identity, current company, seniority / departments /
                    functions, and the VERIFIED email (we keep Apollo's email).
  • Apify owns    : the résumé depth Apollo lacks — summary/about, experience
                    WITH descriptions + per-role skills, education, skills,
                    certifications, languages (+ honors/projects/publications).

``merge_enriched`` produces a single ``enrichedData`` dict whose ``profile``
sub-object matches exactly the shape the matching engine consumes
(``matching_service._score_candidate`` / ``_embed_text_from_profile``):
``fullName, location, totalYears, currentTitle, skills[], titles[],
experience[], education[], certifications[]``. The full raw actor item is kept
under ``enrichedData.raw`` so nothing is lost.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────────

def _s(v: Any) -> str:
    """Coerce to a clean string ('' for None). LinkedIn/actor text arrives with
    HTML entities (e.g. 'Neo4j &amp; LLM'); unescape so the UI shows real chars."""
    return html.unescape(str(v).strip()) if v is not None else ""


def _skill_name(sk: Any) -> str:
    """A skill entry may be a bare string or a dict like {'name': 'Python'}."""
    if isinstance(sk, str):
        return html.unescape(sk.strip())
    if isinstance(sk, dict):
        return _s(sk.get("name") or sk.get("skill") or sk.get("title"))
    return ""


def _date_key(d: Optional[Dict[str, Any]]) -> Optional[str]:
    """Apify date dict {'month':'Nov','year':2022,'text':'Nov 2022'} → '2022-11'.

    Returns None for an open/absent bound (e.g. endDate text 'Present').
    """
    if not isinstance(d, dict):
        return None
    year = d.get("year")
    if not year:
        return None
    month = d.get("month")
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    mnum = None
    if isinstance(month, (int, float)):
        mnum = int(month)
    elif isinstance(month, str) and month.strip():
        mnum = months.get(month.strip()[:3].lower())
    return f"{int(year)}-{mnum:02d}" if mnum else str(int(year))


# ──────────────────────────────────────────────────────────────────────────────
# Section parsers (Apify item → matcher shape)
# ──────────────────────────────────────────────────────────────────────────────

def _parse_experience(apify: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apify experience[] → [{title, company_name, location, description,
    skills, starts_at, ends_at}] (matcher reads title + description)."""
    out: List[Dict[str, Any]] = []
    for exp in (apify.get("experience") or []):
        if not isinstance(exp, dict):
            continue
        end = exp.get("endDate") or {}
        out.append({
            "title": _s(exp.get("position")),
            "company_name": _s(exp.get("companyName")),
            "location": _s(exp.get("location")),
            "employment_type": _s(exp.get("employmentType")),
            # matcher's _embed_text_from_profile reads exp["summary"]; mirror the
            # description into both keys so embedding + display both work.
            "summary": _s(exp.get("description")),
            "description": _s(exp.get("description")),
            "skills": [n for n in (_skill_name(s) for s in (exp.get("skills") or [])) if n],
            "starts_at": _date_key(exp.get("startDate")),
            "ends_at": _date_key(end),
            "is_current": (_s(end.get("text")).lower() == "present") if isinstance(end, dict) else False,
        })
    return out


def _parse_education(apify: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Apify education[] → [{school_name, degree_name, field_of_study, dates}].

    Field names are checked defensively — the actor's education shape varies
    (schoolName/school, degree/degreeName, fieldOfStudy/field)."""
    out: List[Dict[str, Any]] = []
    for edu in (apify.get("education") or []):
        if not isinstance(edu, dict):
            continue
        out.append({
            "school_name": _s(edu.get("schoolName") or edu.get("school") or edu.get("title")),
            "degree_name": _s(edu.get("degree") or edu.get("degreeName")),
            "field_of_study": _s(edu.get("fieldOfStudy") or edu.get("field")),
            "starts_at": _date_key(edu.get("startDate")),
            "ends_at": _date_key(edu.get("endDate")),
        })
    return out


def _parse_certifications(apify: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for cert in (apify.get("certifications") or []):
        if isinstance(cert, str):
            out.append({"name": cert.strip(), "authority": ""})
        elif isinstance(cert, dict):
            out.append({
                "name": _s(cert.get("title") or cert.get("name")),
                "authority": _s(cert.get("issuer") or cert.get("authority") or cert.get("companyName")),
            })
    return out


def _parse_languages(apify: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for lang in (apify.get("languages") or []):
        if isinstance(lang, str):
            out.append({"name": lang.strip(), "proficiency": ""})
        elif isinstance(lang, dict):
            out.append({
                "name": _s(lang.get("name") or lang.get("language")),
                "proficiency": _s(lang.get("proficiency")),
            })
    return out


def _collect_skills(apify: Dict[str, Any], experience: List[Dict[str, Any]]) -> List[str]:
    """Union of top-level skills, topSkills, and per-role skills (deduped,
    case-insensitive, order-preserving)."""
    ordered: List[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        n = name.strip()
        key = n.lower()
        if n and key not in seen:
            seen.add(key)
            ordered.append(n)

    # topSkills is usually a "A • B • C" string; skills[] a list.
    top = apify.get("topSkills")
    if isinstance(top, str):
        for part in top.replace("·", "•").split("•"):
            _add(part)
    elif isinstance(top, list):
        for s in top:
            _add(_skill_name(s))

    for s in (apify.get("skills") or []):
        _add(_skill_name(s))

    for exp in experience:
        for s in exp.get("skills") or []:
            _add(s)

    return ordered


def _total_years(experience: List[Dict[str, Any]]) -> Optional[float]:
    """Approximate total professional experience in years from role spans.

    Merges overlapping/concurrent roles (common: multiple positions at one
    company) by taking the union of month-intervals, so concurrent jobs aren't
    double-counted. Returns None when no dated role exists.
    """
    intervals: List[tuple[int, int]] = []  # (start_month_index, end_month_index)
    now = datetime.utcnow()
    now_idx = now.year * 12 + now.month

    for exp in experience:
        s = _from_key(exp.get("starts_at"))
        if s is None:
            continue
        e = _from_key(exp.get("ends_at"))
        end_idx = e if e is not None else now_idx
        if end_idx >= s:
            intervals.append((s, end_idx))

    if not intervals:
        return None

    intervals.sort()
    merged_months = 0
    cur_start, cur_end = intervals[0]
    for s, e in intervals[1:]:
        if s <= cur_end:  # overlap → extend
            cur_end = max(cur_end, e)
        else:
            merged_months += (cur_end - cur_start)
            cur_start, cur_end = s, e
    merged_months += (cur_end - cur_start)
    return round(merged_months / 12.0, 1)


def _from_key(key: Optional[str]) -> Optional[int]:
    """'2022-11' or '2022' → absolute month index (year*12+month)."""
    if not key:
        return None
    if "-" in key:
        y, m = key.split("-")
        return int(y) * 12 + int(m)
    return int(key) * 12 + 6


def current_role_tenure_years(profile: Dict[str, Any]) -> Optional[float]:
    """Years at the candidate's CURRENT employer, from the experience entry
    ``is_current`` marks (its ``starts_at`` to now).

    Returns ``None`` when there's no current role, or no start date to measure
    from — a missing date is "unknown", never "zero" or "long enough to flag".
    Callers doing tenure-based ranking must treat ``None`` as neutral, per the
    same "a false drop is unrecoverable, missing data is not evidence" rule
    prescreen already follows elsewhere in sourcing.
    """
    experience = profile.get("experience") or []
    current = next(
        (e for e in experience if isinstance(e, dict) and e.get("is_current")), None)
    if not current:
        return None
    start_idx = _from_key(current.get("starts_at"))
    if start_idx is None:
        return None
    now = datetime.utcnow()
    now_idx = now.year * 12 + now.month
    months = max(0, now_idx - start_idx)
    return round(months / 12.0, 1)


# ──────────────────────────────────────────────────────────────────────────────
# Recency / stability / currency signals
#
# Kastell's manual screening of "Sales Manager SAP Retail" candidates
# (2026-07-30) named three patterns the scorer had no way to see: a domain
# match that is only true of a role the candidate left 15-20 years ago
# (Dietmar Schütze, Diana Schroeder); a career that moves jobs/topics fast
# enough to read as unstable, with his own proposed threshold (Ron Porcello —
# "average moving rate ... >2 years"); and a candidate who is simply no longer
# in the workforce (Christian Koch — "in pension ... since 2020"). All three
# are computable from data already captured per experience entry
# (`starts_at`/`ends_at`/`is_current`, from `_parse_experience` above) but
# were never used past sourcing.
# ──────────────────────────────────────────────────────────────────────────────

_RECENT_MONTHS_DEFAULT = 24  # a role that ended within ~2 years still describes who the candidate is today


def recency_tier(
    exp: Dict[str, Any], *, now_idx: Optional[int] = None, recent_months: int = _RECENT_MONTHS_DEFAULT,
) -> str:
    """Classify one experience entry's currency.

    ``"current"`` (is_current) / ``"recent"`` (ended within `recent_months`) /
    ``"historical"`` (older) / ``"unknown"`` (no end date to judge from — kept
    NEUTRAL, treated as non-historical, since a missing date must never read
    as staleness it cannot evidence).
    """
    if not isinstance(exp, dict):
        return "unknown"
    if exp.get("is_current"):
        return "current"
    end_idx = _from_key(exp.get("ends_at"))
    if end_idx is None:
        return "unknown"
    if now_idx is None:
        now = datetime.utcnow()
        now_idx = now.year * 12 + now.month
    return "recent" if (now_idx - end_idx) <= recent_months else "historical"


_MIN_TENURE_SAMPLES = 2  # one dated role can't evidence a "pattern" of moving jobs

def average_tenure_years(profile: Dict[str, Any], *, last_n: int = 5) -> Optional[float]:
    """Mean duration (years) across the candidate's last `last_n` DATED
    positions — Kastell's own proposed job-hopper detector ("if a candidate
    has an average moving rate of >2years, he might be a B candidate").

    Returns ``None`` with fewer than 2 dated positions: one data point cannot
    evidence a pattern, and per this codebase's rule a missing signal is
    neutral, never a downgrade.
    """
    experience = profile.get("experience") or []
    now = datetime.utcnow()
    now_idx = now.year * 12 + now.month
    spans: List[Tuple[int, int]] = []
    for exp in experience:
        if not isinstance(exp, dict):
            continue
        start_idx = _from_key(exp.get("starts_at"))
        if start_idx is None:
            continue
        end_idx = _from_key(exp.get("ends_at"))
        if end_idx is None:
            end_idx = now_idx if exp.get("is_current") else None
        if end_idx is None or end_idx < start_idx:
            continue
        spans.append((start_idx, end_idx))
    if len(spans) < _MIN_TENURE_SAMPLES:
        return None
    # Most recent role first — "last N roles" means the N most recent, not
    # however the actor happened to order them.
    spans.sort(key=lambda pair: pair[0], reverse=True)
    sample = spans[:last_n]
    months = [end - start for start, end in sample]
    return round((sum(months) / len(months)) / 12.0, 1)


_INACTIVE_MONTHS_DEFAULT = 24
_RETIREMENT_MARKERS = {
    "retired", "retirement", "im ruhestand", "ruhestand", "pensionär",
    "pensionärin", "pensioniert", "im rente", "rentner", "rentnerin",
}

def inactive_candidate_flag(
    profile: Dict[str, Any], *, inactive_months: int = _INACTIVE_MONTHS_DEFAULT,
) -> Optional[Dict[str, Any]]:
    """Detect a candidate who is likely no longer active in the workforce
    (Christian Koch — "he is in pension and seems to be out since 2020").

    Two independent signals, either sufficient:
      * self-reported retirement language in headline/summary
      * no current role AND the most recent role ended more than
        `inactive_months` ago

    Returns ``None`` (the neutral default) when neither fires — including when
    there is simply no dated history to check. Absence of evidence is not
    evidence of retirement; a false "inactive" on missing/bad data is exactly
    the unrecoverable false-DROP this codebase avoids everywhere else.
    """
    blob = f"{profile.get('headline') or ''} {profile.get('summary') or ''}".lower()
    if any(marker in blob for marker in _RETIREMENT_MARKERS):
        return {"inactive": True, "reason": "Profile headline/summary reads as retired."}

    experience = profile.get("experience") or []
    if any(isinstance(e, dict) and e.get("is_current") for e in experience):
        return None  # has a current role — clearly active

    now = datetime.utcnow()
    now_idx = now.year * 12 + now.month
    end_idxs = [
        idx for idx in (_from_key(e.get("ends_at")) for e in experience if isinstance(e, dict))
        if idx is not None
    ]
    if not end_idxs:
        return None  # no dated history at all — nothing to judge from
    months_since = now_idx - max(end_idxs)
    if months_since > inactive_months:
        years = round(months_since / 12.0)
        return {
            "inactive": True,
            "reason": f"No current role found; the most recent position ended about {years} year(s) ago.",
        }
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Public: merge
# ──────────────────────────────────────────────────────────────────────────────

def merge_enriched(
    apollo_person: Optional[Dict[str, Any]],
    apify_profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge an Apollo person record with an Apify profile item.

    ``apollo_person`` may be None (enriching a bare LinkedIn URL with no Apollo
    row). Returns the ``enrichedData`` dict to store on the candidate:

        {
          "profile":  { … matcher-ready shape … },
          "contact":  { email, phone, linkedin },
          "source":   { "apollo": bool, "apify": bool },
          "raw":      { full Apify item (+ apollo kept on the candidate doc) },
          "enrichedAt": <set by caller>,
        }
    """
    apollo = apollo_person or {}
    org = apollo.get("organization") or {}

    experience = _parse_experience(apify_profile)
    education = _parse_education(apify_profile)
    certifications = _parse_certifications(apify_profile)
    languages = _parse_languages(apify_profile)
    skills = _collect_skills(apify_profile, experience)

    # Current title: prefer Apify (its current position / headline), fall back to
    # Apollo's title. Apify `currentPosition` is a list of {companyName,...}.
    current_positions = apify_profile.get("currentPosition") or []
    current_company = ""
    if isinstance(current_positions, list) and current_positions:
        current_company = _s((current_positions[0] or {}).get("companyName"))
    # The current role's title lives in experience[is_current]; else headline.
    current_exp = next((e for e in experience if e.get("is_current")), None)
    current_title = (
        (current_exp or {}).get("title")
        or _s(apify_profile.get("headline"))
        or _s(apollo.get("title"))
    )
    current_company = current_company or (
        (current_exp or {}).get("company_name") or _s(org.get("name"))
    )

    # Name / location: Apify first, Apollo fallback.
    first = _s(apify_profile.get("firstName")) or _s(apollo.get("first_name"))
    last = _s(apify_profile.get("lastName")) or _s(apollo.get("last_name"))
    full_name = f"{first} {last}".strip() or _s(apollo.get("name"))

    loc = apify_profile.get("location") or {}
    location = ""
    if isinstance(loc, dict):
        location = _s(loc.get("linkedinText")) or _s((loc.get("parsed") or {}).get("text"))
    if not location:
        location = ", ".join(
            p for p in (apollo.get("city"), apollo.get("state"), apollo.get("country")) if p
        )

    profile = {
        "fullName": full_name,
        "firstName": first,
        "lastName": last,
        "headline": _s(apify_profile.get("headline")) or _s(apollo.get("headline")),
        "summary": _s(apify_profile.get("about")),
        "location": location,
        "currentTitle": current_title,
        "currentCompany": current_company,
        "totalYears": _total_years(experience),
        "skills": skills,
        # `titles` feeds the embedding text; list every role title held.
        "titles": [e["title"] for e in experience if e.get("title")],
        "experience": experience,
        "education": education,
        "certifications": certifications,
        "languages": languages,
        # LinkedIn's own "open to work" flag from the scraped profile — a
        # recruiter-facing signal, so it survives the merge instead of living
        # only in raw.apify where nothing reads it.
        "openToWork": bool(apify_profile.get("openToWork")),
    }

    contact = {
        # Keep Apollo's verified email; Apify profile-only mode returns none.
        "email": _s(apollo.get("email")) or None,
        # The profile scrape carries a phone when the member lists one publicly
        # (usually null). Apollo's projection here has none, so Apify is the
        # only source.
        "phone": _s(apify_profile.get("phone")) or None,
        "linkedin": _s(apify_profile.get("linkedinUrl")) or _s(apollo.get("linkedin_url")) or None,
        "emailStatus": _s(apollo.get("email_status")) or None,
    }

    return {
        "profile": profile,
        "contact": contact,
        "source": {"apollo": bool(apollo_person), "apify": True},
        "raw": {"apify": apify_profile},
    }
