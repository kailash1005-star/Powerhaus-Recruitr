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
from typing import Any, Dict, List, Optional


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
    }

    contact = {
        # Keep Apollo's verified email; Apify profile-only mode returns none.
        "email": _s(apollo.get("email")) or None,
        "phone": None,  # neither source reliably provides candidate mobile here
        "linkedin": _s(apify_profile.get("linkedinUrl")) or _s(apollo.get("linkedin_url")) or None,
        "emailStatus": _s(apollo.get("email_status")) or None,
    }

    return {
        "profile": profile,
        "contact": contact,
        "source": {"apollo": bool(apollo_person), "apify": True},
        "raw": {"apify": apify_profile},
    }
