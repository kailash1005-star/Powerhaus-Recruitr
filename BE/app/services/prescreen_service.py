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

# A token has to be this long to carry meaning ("de", "of", "&" do not).
_MIN_TOKEN = 3
# Per-token fuzzy threshold. German compounds inflect across a posting and a
# profile ("Entgeltabrechner" vs "Entgeltabrechnung", "Berater" vs "Beratung"),
# so an exact token match alone under-counts real hits.
_TOKEN_FUZZ = 82


def tokens(text: str) -> List[str]:
    """Significant lowercase tokens of a title/phrase. Public: the Broadener's
    domain guard shares it, so the gate and the search can't drift apart on what
    counts as the same word."""
    return [t for t in re.split(r"[^\w]+", (text or "").lower(), flags=re.UNICODE) if len(t) >= _MIN_TOKEN]


def token_present(needle: str, hay: List[str]) -> bool:
    for h in hay:
        if needle == h or needle in h or h in needle:
            return True
        if fuzz.ratio(needle, h) >= _TOKEN_FUZZ:
            return True
    return False


def _phrase_overlap(title_tokens: List[str], phrase: str) -> float:
    """Fraction of `phrase`'s significant tokens that appear in the title."""
    want = tokens(phrase)
    if not want:
        return 0.0
    hit = sum(1 for w in want if token_present(w, title_tokens))
    return hit / len(want)


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
