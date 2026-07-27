"""The cheap gate: judge a search hit BEFORE paying to enrich it.

Where the money goes
--------------------
A LinkedIn people-search returns a short profile — `currentTitle`,
`currentCompany`, `location` — for free. Turning that into a scoreable candidate
means a per-profile Apify scrape. The pipeline used to enrich EVERY hit and only
then discover, in the matcher, that none of them carried a single must-have skill.
The actor fuzzy-OR-matches titles and the broadening ladder deliberately widens
them, so a chunk of any result set is slop the search never meant to return.

This module spends the free signal first: a hit whose title has nothing to do with
the role is dropped before it costs anything.

Precision, not recall
---------------------
A false DROP is unrecoverable — that candidate is never enriched, never scored,
never seen again. A false KEEP just costs one scrape and gets caught by the real
matcher a minute later. So this gate is deliberately lopsided: it only rejects
what it is confident about, and anything arguable is kept and passed downstream.
It answers "is this person plausibly in the right job family?", NOT "does this
person meet the requirements" — a title can't evidence a skill list, and pretending
otherwise here would throw away good people.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# A token has to be this long to carry meaning ("de", "of", "&" do not) —
# UNLESS it is a known domain code (see _SHORT_KEEP). A two-letter SAP module
# ("CO", "PS") is the single most discriminating word in this space, so the raw
# length filter can't be allowed to silently delete it.
_MIN_TOKEN = 3
# Per-token fuzzy threshold. German compounds inflect across a posting and a
# profile ("Entgeltabrechner" vs "Entgeltabrechnung", "Berater" vs "Beratung"),
# so an exact token match alone under-counts real hits.
_TOKEN_FUZZ = 82
# Substring containment ("co" in "consultant") is only allowed for tokens this
# long. Below it, a short token must match EXACTLY — otherwise "co"/"ps"/"fi"
# match inside "consultant"/"operations"/"office" and every SAP profile looks
# like every other one.
_SUBSTR_MIN = 4

# Short but load-bearing: SAP module/solution codes and a few tech acronyms.
# These survive the length filter AND are treated as the role's discriminator
# (see _MODULE_CODES). Extend as new specialties appear.
_MODULE_CODES = {
    "co", "ps", "fi", "mm", "sd", "pp", "qm", "pm", "wm", "hr", "hcm", "ewm",
    "le", "cs", "tm", "gts", "bw", "bi", "pi", "po", "mdg", "ec", "fico",
    "sf", "bpc", "grc", "vim", "ppm", "is", "vc", "pa", "om", "ff",
}
_SHORT_KEEP = _MODULE_CODES | {"ai", "ml", "qa", "ux", "ui", "it", "bi"}

# Seniority / role-shape words. Every candidate in the family shares them, so a
# match on these alone is NOT evidence of the right specialty — they carry a
# fraction of the weight a discriminating term does.
_GENERIC = {
    "senior", "junior", "lead", "leitung", "leiter", "leiterin", "head",
    "consultant", "consultants", "consulting", "berater", "beraterin",
    "beraterinnen", "beratung", "manager", "managerin", "management",
    "inhouse", "specialist", "spezialist", "spezialistin", "expert", "experte",
    "expertin", "associate", "principal", "professional", "sap", "erp",
}
# Relative weight of a discriminating token vs a generic one in the overlap.
_SPECIFIC_W = 3.0
_GENERIC_W = 1.0
# When the target names module code(s) and the title carries NONE of them, the
# person is in a different specialty — cap the match here regardless of how many
# generic words line up.
_WRONG_MODULE_CAP = 0.35


def tokens(text: str) -> List[str]:
    """Significant lowercase tokens of a title/phrase. Public: the Broadener's
    domain guard shares it, so the gate and the search can't drift apart on what
    counts as the same word. Keeps known short domain codes ("co", "ps")."""
    out: List[str] = []
    for t in re.split(r"[^\w]+", (text or "").lower(), flags=re.UNICODE):
        if len(t) >= _MIN_TOKEN or t in _SHORT_KEEP:
            out.append(t)
    return out


# Cross-language / role-form equivalences, collapsed to one canonical token. In
# the DACH market a real SAP candidate is a "Berater", not a "Consultant" — the
# two must match or every German title under-scores against an English target.
_SYNONYMS = {
    "berater": "consultant", "beraterin": "consultant",
    "beraterinnen": "consultant", "beratung": "consultant",
    "consulting": "consultant", "consultants": "consultant",
    "spezialist": "specialist", "spezialistin": "specialist",
    "experte": "expert", "expertin": "expert",
    "entwickler": "developer", "leiter": "lead", "leiterin": "lead",
    "leitung": "lead", "manager": "manager", "managerin": "manager",
}


def _canon(tok: str) -> str:
    return _SYNONYMS.get(tok, tok)


def token_present(needle: str, hay: List[str]) -> bool:
    needle_c = _canon(needle)
    for h in hay:
        h_c = _canon(h)
        if needle == h or needle_c == h_c:
            return True
        # Substring / fuzzy only for longer tokens — short codes must be exact
        # so "co" never matches inside "consultant".
        if len(needle_c) >= _SUBSTR_MIN and len(h_c) >= _SUBSTR_MIN:
            if needle_c in h_c or h_c in needle_c:
                return True
            if fuzz.ratio(needle_c, h_c) >= _TOKEN_FUZZ:
                return True
    return False


def _weight(tok: str) -> float:
    return _GENERIC_W if tok in _GENERIC else _SPECIFIC_W


def _phrase_overlap(title_tokens: List[str], phrase: str) -> float:
    """Weighted fraction of `phrase`'s tokens present in the title.

    Discriminating tokens (a module code, a domain word) count for more than
    generic seniority words, and a title that misses every module code the
    phrase asked for is capped — a generic "Senior SAP Consultant" can no longer
    read as a perfect "SAP CO Consultant" match.
    """
    want = tokens(phrase)
    if not want:
        return 0.0
    total = sum(_weight(w) for w in want)
    hit = sum(_weight(w) for w in want if token_present(w, title_tokens))
    frac = hit / total if total else 0.0

    want_modules = [w for w in want if w in _MODULE_CODES]
    if want_modules and not any(token_present(m, title_tokens) for m in want_modules):
        frac = min(frac, _WRONG_MODULE_CAP)
    return frac


# Owner/executive titles. A person carrying one of these is running a business,
# not doing the hands-on specialty — the "cofounder shows up as an SAP CO
# consultant" leak. The keyword channel (which matches profile TEXT, not the
# title) pulls these in because their profile name-drops the tool; this set lets
# the gate refuse the keyword rescue for them. Never blocks a genuine title
# match (that path scores high and is kept before any of this runs).
_EXEC_TOKENS = {
    "ceo", "cfo", "cto", "coo", "cio", "cmo", "chairman", "chairwoman",
    "president", "vorstand", "geschäftsführer", "geschäftsführerin",
    "geschäftsführung", "geschäftsinhaber", "inhaber", "inhaberin",
    "owner", "founder", "gründer", "gründerin", "mitgründer", "mitgründerin",
    "selbständig", "selbstständig", "freiberufler", "freelancer",
}


def is_executive_title(title: str) -> bool:
    """True if the title is an owner/executive/self-employed role."""
    return bool(set(tokens(title)) & _EXEC_TOKENS)


# ── Deterministic seniority / role-form hardening ────────────────────────────
#
# Title-overlap alone gives a "Junior Process Consultant SAP HCM" and a "Senior
# SAP HCM Consultant" the SAME 100 — they share every module token. But a role
# that asks for a Specialist/Senior/Lead does not want a working student, and a
# hands-on specialty is not a Key User (an end-user) or a pure people-manager.
# These are DETERMINISTIC facts about the title, so they belong in the score,
# applied consistently to every candidate — not left to the LLM to catch some
# and miss others.

# Candidate is early-career / not the hands-on specialist the role wants.
_JUNIOR_MARKERS = {
    "junior", "jr", "werkstudent", "werkstudentin", "praktikant", "praktikantin",
    "praktikum", "intern", "internship", "trainee", "azubi", "auszubildende",
    "auszubildender", "ausbildung", "studium", "student", "studentin", "dual",
    "aushilfe", "volontär", "volontariat", "entry",
}
# End-user / power-user of the system, not a consultant/specialist BUILDING on it.
_ENDUSER_MARKERS = {"keyuser", "anwender", "endanwender", "enduser", "sachbearbeiter"}
# The role, by its own title/seniority, is asking for an experienced specialist.
_SENIOR_ROLE_MARKERS = {
    "senior", "specialist", "spezialist", "spezialistin", "lead", "leitung",
    "principal", "expert", "experte", "expertin", "architekt", "architect",
    "head", "manager",
}


def _role_wants_experience(target_titles, requirements) -> bool:
    text = " ".join(target_titles or [])
    text += " " + str((requirements or {}).get("title") or "")
    text += " " + str((requirements or {}).get("seniority") or "")
    return bool(set(tokens(text)) & _SENIOR_ROLE_MARKERS)


def seniority_fit(title: str, target_titles=None, requirements=None) -> float:
    """A 0–1 multiplier on the title-overlap score for seniority/role-form.

    1.0 when the candidate's level fits (or the role names no seniority). A hard
    cut when the role wants a specialist/senior but the candidate is a
    student/junior or an end-user/key-user — the exact "a junior scores the same
    as a senior" flaw."""
    cand = set(tokens(title))
    is_junior = bool(cand & _JUNIOR_MARKERS)
    is_enduser = bool(cand & _ENDUSER_MARKERS) or ("key" in cand and "user" in cand)
    if not (is_junior or is_enduser):
        return 1.0
    # Only a role that explicitly asks for a specialist/senior/lead penalises a
    # junior or end-user — a plain "Consultant" role accepts a junior consultant,
    # so it must not be down-ranked.
    if _role_wants_experience(target_titles, requirements):
        return 0.40 if is_junior else 0.50
    return 1.0


def score_profile(
    profile: Dict[str, Any],
    *,
    requirements: Optional[Dict[str, Any]] = None,
    target_titles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Rate one search hit on the free signal alone.

    Returns {score 0..100, roleFit, reasons, matchedVia} — no decision, so the
    caller owns the threshold and this stays testable.
    """
    requirements = requirements or {}
    targets = [t for t in (target_titles or []) if t]
    title = (profile.get("currentTitle") or "").strip()
    title_tokens = tokens(title)

    reasons: List[str] = []
    if not title:
        # No title is not evidence of a bad candidate — the actor just didn't
        # return one. Never drop on absent signal.
        return {"score": 50.0, "roleFit": 0.5, "matchedVia": None,
                "reasons": ["No title on the search result — kept for enrichment to decide."]}

    best, via, kind = 0.0, None, None

    # 1. The titles the search actually aimed at. Strongest available signal:
    # the Strategist already translated the role into real LinkedIn headlines.
    for t in targets:
        r = _phrase_overlap(title_tokens, t)
        if r > best:
            best, via, kind = r, t, "target title"

    # 2. A must-have skill named IN the title is direct domain evidence
    # ("Personalsachbearbeiter Entgeltabrechnung" for an Entgeltabrechnung role).
    for m in (requirements.get("mustHaveSkills") or []):
        if _phrase_overlap(title_tokens, m) >= 1.0:
            if 1.0 > best:
                best, via, kind = 1.0, m, "must-have skill in title"
            break

    # 3. The role's own title, when the search aimed at nothing usable.
    if requirements.get("title"):
        r = _phrase_overlap(title_tokens, str(requirements["title"]))
        if r > best:
            best, via, kind = r, requirements["title"], "job title"

    if via:
        reasons.append(f"Title “{title}” matches {kind} “{via}” ({best:.0%} of its terms).")
    else:
        reasons.append(f"Title “{title}” shares no vocabulary with this role.")

    # Deterministic seniority / role-form modifier: a junior/student or an
    # end-user/key-user for an experienced-specialist role is scored down, so a
    # junior and a senior with the same module tokens no longer tie at 100.
    sfit = seniority_fit(title, target_titles=targets, requirements=requirements)
    if sfit < 1.0 and via:
        cand = set(title_tokens)
        which = ("early-career (junior/working-student)"
                 if cand & _JUNIOR_MARKERS else "an end-user / key-user")
        reasons.append(
            f"Scored down: the title reads as {which}, but the role asks for an "
            f"experienced specialist.")
    best *= sfit

    return {"score": round(best * 100, 1), "roleFit": round(best, 3),
            "matchedVia": via, "reasons": reasons}


def screen(
    profile: Dict[str, Any],
    *,
    requirements: Optional[Dict[str, Any]] = None,
    target_titles: Optional[List[str]] = None,
    min_score: float = 25.0,
) -> Tuple[bool, Dict[str, Any]]:
    """(keep?, verdict). Drops only hits scoring below `min_score`.

    With no requirements AND no target titles there is nothing to judge against,
    so everything is kept — a missing role spec must never silently empty a
    recruiter's pipeline.
    """
    if not (target_titles or (requirements or {}).get("mustHaveSkills")
            or (requirements or {}).get("title")):
        return True, {"score": 50.0, "roleFit": 0.5, "decision": "keep", "matchedVia": None,
                      "reasons": ["No role spec to screen against — kept."]}

    verdict = score_profile(profile, requirements=requirements, target_titles=target_titles)
    keep = verdict["score"] >= min_score
    verdict["decision"] = "keep" if keep else "drop"
    return keep, verdict
