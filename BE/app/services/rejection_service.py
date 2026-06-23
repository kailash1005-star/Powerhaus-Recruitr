"""Rejection services — title filtering for jobs and prospect filtering for Apollo results."""

import logging
import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Iterable, Tuple

from rapidfuzz import fuzz

from app.config import (
    ACCEPTED_TITLE_KEYWORDS,
    REJECTED_TITLE_KEYWORDS,
    HR_KEYWORDS,
    CSUITE_SENIORITIES,
    WANTED_FUNCTIONS,
    UNWANTED_FUNCTIONS,
    INDUSTRY_PERSONA_MAP,
    INDUSTRY_UNWANTED_EXCLUSIONS,
    DEFAULT_PERSONA_TITLES,
    normalize_industry_name,
)

logger = logging.getLogger(__name__)

# Shared regex patterns for prospect filtering
JUNIOR_TITLE_RE = re.compile(
    r"\bassistant\b|\bcoordinator\b|\bspecialist\b|\banalyst\b|\bintern\b"
)
EXECUTIVE_TITLE_RE = re.compile(
    r"\bchief\b|\bexecutive director\b|\bgeneral manager\b|\bmanaging director\b"
)
SENIOR_TITLE_RE = re.compile(r"\bdirector\b|\bhead\b|\bvp\b|\bvice president\b")


class JobTitleRejectionService:
    """Evaluates job titles and returns acceptance + reason."""

    def __init__(
        self,
        accepted_keywords: Iterable[str] | None = None,
        rejected_keywords: Iterable[str] | None = None,
        fuzzy_threshold: float = 0.86,
    ) -> None:
        accepted = accepted_keywords if accepted_keywords is not None else ACCEPTED_TITLE_KEYWORDS
        rejected = rejected_keywords if rejected_keywords is not None else REJECTED_TITLE_KEYWORDS
        self.accepted_keywords = [k.strip().lower() for k in accepted if k]
        self.rejected_keywords = [k.strip().lower() for k in rejected if k]
        self.fuzzy_threshold = fuzzy_threshold

    def evaluate_title(self, title: str) -> Tuple[bool, str]:
        normalized_title = (title or "").strip().lower()
        if not normalized_title:
            return False, "Missing job title"

        if self._matches_any(normalized_title, self.rejected_keywords):
            return False, "Title matched rejected keywords"

        if self._matches_any(normalized_title, self.accepted_keywords):
            return True, ""

        return False, "Title not in accepted keywords"

    def _matches_any(self, text: str, keywords: list[str]) -> bool:
        for keyword in keywords:
            if keyword in text:
                return True
            ratio = SequenceMatcher(None, text, keyword).ratio()
            if ratio >= self.fuzzy_threshold:
                return True
            for token in text.split():
                if SequenceMatcher(None, token, keyword).ratio() >= self.fuzzy_threshold:
                    return True
        return False


# ---------------------------------------------------------------------------
# Prospect Pre-Filter (coarse, before enrichment)
# ---------------------------------------------------------------------------

class ProspectPreFilter:
    """Coarse filter on Apollo search results before any further processing."""

    def __init__(self, industry_name: str | None = None) -> None:
        self.industry_name = industry_name or ""
        key = normalize_industry_name(self.industry_name)
        self.personas = INDUSTRY_PERSONA_MAP.get(key, DEFAULT_PERSONA_TITLES)

    def filter(self, prospects: list[dict]) -> tuple[list[dict], list[dict]]:
        accepted: list[dict] = []
        rejected: list[dict] = []

        for p in prospects:
            title = p.get("title") or ""
            seniority = p.get("seniority") or ""
            name = p.get("name") or p.get("id") or "unknown"

            if not title:
                p["_rejection_reason"] = "no title"
                p["_filter_step"] = "pre_filter_rejected"
                rejected.append(p)
                continue

            t = title.lower()

            if JUNIOR_TITLE_RE.search(t):
                p["_rejection_reason"] = "junior title keyword"
                p["_filter_step"] = "pre_filter_rejected"
                rejected.append(p)
                continue

            has_hr = _title_has_keywords(title, HR_KEYWORDS)
            has_persona = _fuzzy_match(title, self.personas) or _fuzzy_match(title, DEFAULT_PERSONA_TITLES)
            is_exec = seniority.lower() in CSUITE_SENIORITIES or bool(EXECUTIVE_TITLE_RE.search(t))

            if has_hr or has_persona or is_exec:
                accepted.append(p)
            else:
                p["_rejection_reason"] = "no hr keyword, no persona match, not executive"
                p["_filter_step"] = "pre_filter_rejected"
                rejected.append(p)

        logger.info("Pre-filter: %d → %d accepted, %d rejected", len(prospects), len(accepted), len(rejected))
        return accepted, rejected


# ---------------------------------------------------------------------------
# Prospect Post-Filter (persona extraction, after pre-filter or enrichment)
# ---------------------------------------------------------------------------

class ProspectPostFilter:
    """Persona extraction + buyer relevance check."""

    def __init__(self, industry_name: str | None = None) -> None:
        self.industry_name = industry_name or ""
        key = normalize_industry_name(self.industry_name)
        self.personas = INDUSTRY_PERSONA_MAP.get(key, DEFAULT_PERSONA_TITLES)
        exclusions = INDUSTRY_UNWANTED_EXCLUSIONS.get(key, [])
        self._unwanted = [uf for uf in UNWANTED_FUNCTIONS if uf not in exclusions]

    def extract_personas(self, prospects: list[dict]) -> tuple[list[dict], list[dict]]:
        selected: dict[str, dict] = {}
        rejected: list[dict] = []

        for p in prospects:
            pid = p.get("id") or ""
            title = p.get("title") or ""
            sen = p.get("seniority") or ""

            if not title:
                p["_rejection_reason"] = "no title"
                p["_filter_step"] = "post_filter_rejected"
                rejected.append(p)
                continue

            if not self._is_relevant_buyer(title, sen, p):
                reason = self._rejection_reason(title, p)
                p["_rejection_reason"] = reason
                p["_filter_step"] = "post_filter_rejected"
                rejected.append(p)
                continue

            reasons = self._match_reasons(title, sen)
            if reasons and pid not in selected:
                p["_match_reasons"] = reasons
                p["_seniority_weight"] = _seniority_weight(title, sen)
                p["_filter_step"] = "selected"
                selected[pid] = p

        accepted = sorted(selected.values(), key=lambda x: x["_seniority_weight"])
        deduped, dedup_rejected = _deduplicate_by_title(accepted)
        for d in dedup_rejected:
            d["_rejection_reason"] = "dedup: max per title exceeded"
            d["_filter_step"] = "post_filter_rejected"
        rejected.extend(dedup_rejected)
        logger.info("Post-filter: %d accepted, %d rejected", len(deduped), len(rejected))
        return deduped, rejected

    def _is_relevant_buyer(self, title: str, seniority: str, person: dict) -> bool:
        t = title.lower()
        s = seniority.lower()

        if JUNIOR_TITLE_RE.search(t):
            return False

        if _fuzzy_match(title, self.personas) or _fuzzy_match(title, DEFAULT_PERSONA_TITLES):
            return True

        if s in CSUITE_SENIORITIES or EXECUTIVE_TITLE_RE.search(t):
            if self._has_unwanted_function(person) and not _has_wanted_function(person):
                return False
            return True

        if SENIOR_TITLE_RE.search(t):
            if any(fn in t for fn in WANTED_FUNCTIONS):
                return True
            if _has_wanted_function(person):
                return True
            return False

        return False

    def _has_unwanted_function(self, person: dict) -> bool:
        funcs = [f.lower() for f in (person.get("functions") or [])]
        return any(uf in f for f in funcs for uf in self._unwanted)

    def _match_reasons(self, title: str, seniority: str) -> list[str]:
        reasons: list[str] = []
        t = title.lower()
        if _title_has_keywords(title, HR_KEYWORDS):
            reasons.append("hr_talent_lead")
        if seniority in CSUITE_SENIORITIES or re.search(
            r"\bchief\b|\bceo\b|\bcoo\b|\bcfo\b|\bcto\b|\bchro\b", t
        ):
            reasons.append("csuite")
        if re.search(r"\bexecutive director\b", t):
            reasons.append("executive_director")
        if _fuzzy_match(title, self.personas):
            reasons.append("industry_persona_match")
        if _fuzzy_match(title, DEFAULT_PERSONA_TITLES):
            reasons.append("default_persona_match")
        return reasons

    def _rejection_reason(self, title: str, person: dict) -> str:
        t = title.lower()
        if JUNIOR_TITLE_RE.search(t):
            return "junior title keyword"
        if not EXECUTIVE_TITLE_RE.search(t) and not SENIOR_TITLE_RE.search(t):
            return "no senior-level keyword"
        if self._has_unwanted_function(person) and not _has_wanted_function(person):
            funcs = [f.lower() for f in (person.get("functions") or [])]
            return f"unwanted function: {funcs}"
        return "not in HR/Ops function"


# ---------------------------------------------------------------------------
# Module-level helpers (shared by PreFilter / PostFilter)
# ---------------------------------------------------------------------------

def _fuzzy_match(title: str, persona_list: list[str], threshold: int = 85) -> bool:
    t = title.lower()
    return any(fuzz.token_set_ratio(p.lower(), t) >= threshold for p in persona_list)


def _title_has_keywords(title: str, keywords: list[str]) -> bool:
    t = title.lower()
    return any(kw in t for kw in keywords)


def _has_wanted_function(person: dict) -> bool:
    funcs = [f.lower() for f in (person.get("functions") or [])]
    return any(wf in f for f in funcs for wf in WANTED_FUNCTIONS)


def _seniority_weight(title: str, seniority: str) -> int:
    t = title.lower()
    s = seniority.lower()
    if s in CSUITE_SENIORITIES or re.search(r"\bchief\b|\bceo\b|\bcoo\b|\bcfo\b|\bcto\b|\bchro\b", t):
        return 1
    if re.search(r"\bexecutive director\b|\bmanaging director\b", t):
        return 2
    if re.search(r"\bvp\b|\bvice president\b", t):
        return 3
    if re.search(r"\bdirector\b", t):
        return 4
    if re.search(r"\bhead\b", t):
        return 5
    return 6


def _deduplicate_by_title(selected: list[dict], max_per_title: int = 2) -> tuple[list[dict], list[dict]]:
    seen: defaultdict[str, int] = defaultdict(int)
    kept: list[dict] = []
    dropped: list[dict] = []
    for p in selected:
        norm = re.sub(r"^(senior|regional|assistant)\s+", "", p.get("title", "").lower()).strip()
        if seen[norm] < max_per_title:
            kept.append(p)
            seen[norm] += 1
        else:
            dropped.append(p)
    return kept, dropped
