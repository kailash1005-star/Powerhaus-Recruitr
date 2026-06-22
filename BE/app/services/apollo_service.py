"""
Apollo Service — prospect search (no enrichment in the default pipeline).
Enrichment functions are kept for later on-demand use but the orchestrator
calls find_prospects(..., enrich=False).

Apollo API credit model (per https://docs.apollo.io/docs/api-pricing and
https://docs.apollo.io/reference/people-api-search — verified May 2026):

  FREE — no credits consumed:
    * People Search  POST /mixed_people/api_search   (search_by_titles / search_by_seniority)
      "This endpoint is optimized for API usage and does not consume credits."
      It returns people WITHOUT email/phone (contact info is masked/locked).

  CREDIT-CONSUMING (only when we explicitly enrich):
    * People Match       POST /people/match        (_enrich_single)
    * Bulk People Match  POST /people/bulk_match    (_enrich_bulk)
    * Also credit-consuming but NOT used here: Person Details /people/{id},
      Organization Search /mixed_companies/search, Organization Enrichment, etc.

So the background orchestration (Phase 3 title + management/seniority search) is
FREE — it only counts against Apollo's rate limit (HTTP 429), not credits.
Credits are spent solely when enrichment (match/bulk_match) runs, which happens
on-demand via enrich()/find_prospects(enrich=True), never in the background run.
"""
import logging
import re
import time
from typing import Any

import requests

from app.config import (
    APOLLO_BASE_URL,
    APOLLO_PER_PAGE,
    APOLLO_BULK_BATCH_SIZE,
    APOLLO_SENIORITIES,
    INDUSTRY_PERSONA_MAP,
    DEFAULT_PERSONA_TITLES,
    normalize_industry_name,
    settings,
)
from app.services.rejection_service import ProspectPreFilter, ProspectPostFilter

logger = logging.getLogger(__name__)


# Split on " - ", " — ", " – ", or " | " surrounded by whitespace — common
# separators between a job title and a trailing qualifier (location, dept).
_TITLE_SUFFIX_SEP = re.compile(r"\s+[-–—|]\s+")


def _title_variants(title: str) -> list[str]:
    """Generate progressively-relaxed title variants for Apollo fallback search.

    Scraped job titles often include suffixes Apollo doesn't understand
    (e.g. "Head of Investment Placement - UAE") or are too narrow to match
    anyone. The cascade is:

      1. Original
      2. Strip trailing " - <suffix>" (or |, –, —)
      3. Token-shrink: drop trailing tokens one at a time, floor 3 tokens
    """
    variants: list[str] = []
    seen: set[str] = set()

    def _add(t: str) -> None:
        t = t.strip()
        key = t.lower()
        if t and key not in seen:
            variants.append(t)
            seen.add(key)

    _add(title)
    stripped = _TITLE_SUFFIX_SEP.split(title, maxsplit=1)[0]
    _add(stripped)
    tokens = stripped.split()
    while len(tokens) > 3:
        tokens = tokens[:-1]
        _add(" ".join(tokens))
    return variants


class ApolloService:
    """Apollo API client for people search and (optional) enrichment."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.APOLLO_API_KEY
        # Set True when a search exhausts its retries against Apollo's 429 throttle.
        # find_prospects checks this to avoid firing the fallback (which would 429 too).
        self._rate_limited = False

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _request_page(self, params: dict, domain: str, label: str) -> dict | None:
        """POST one search page, retrying on HTTP 429 with backoff.

        Returns the parsed JSON dict, or None on hard failure. Honors the
        ``Retry-After`` header when present. Sets ``self._rate_limited`` when the
        request is abandoned because of sustained 429s.
        """
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            try:
                resp = requests.post(
                    f"{APOLLO_BASE_URL}/mixed_people/api_search",
                    headers=self._headers(),
                    params=params,
                    timeout=30,
                )
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else min(2 ** attempt, 30)
                    logger.warning(
                        "Apollo 429 (%s %s) — backoff %.1fs (attempt %d/%d)",
                        label, domain, wait, attempt, max_attempts,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.error("Apollo %s error at %s: %s", label, domain, e)
                if attempt < max_attempts:
                    time.sleep(min(2 ** attempt, 10))
                    continue
                return None
        # All attempts were 429 retries → throttled.
        self._rate_limited = True
        return None

    def _paged_search(self, extra_params: dict, domain: str, label: str) -> list[dict]:
        """Run a paginated people search with the given filter params."""
        all_people: list[dict] = []
        page = 1
        while True:
            params = {"per_page": APOLLO_PER_PAGE, "page": page, **extra_params}
            data = self._request_page(params, domain, label)
            if data is None:
                break
            people = data.get("people", [])
            total = data.get("total_entries", 0)
            pages = max(1, -(-total // APOLLO_PER_PAGE))
            all_people.extend(people)
            if page >= pages or not people:
                break
            page += 1
            time.sleep(0.5)
        return all_people

    def search_by_titles(self, domain: str, titles: list[str]) -> list[dict]:
        """Primary search — query Apollo with specific job titles."""
        logger.info("Title search: %d title(s) at %s", len(titles), domain)
        return self._paged_search(
            {
                "person_titles[]": titles,
                "include_similar_titles": "true",
                "q_organization_domains_list[]": [domain],
            },
            domain,
            "title search",
        )

    def search_by_seniority(self, domain: str) -> list[dict]:
        """Fallback search — fetch prospects by seniority level."""
        logger.info("Seniority search at %s", domain)
        return self._paged_search(
            {
                "person_seniorities[]": APOLLO_SENIORITIES,
                "q_organization_domains_list[]": [domain],
            },
            domain,
            "seniority search",
        )

    # ------------------------------------------------------------------
    # Candidate search (Phase 4 — recruitment candidate pipelines)
    # ------------------------------------------------------------------

    def search_candidates(
        self,
        *,
        title: str,
        location_country: str | None = None,
        current_industry: str | None = None,
        max_results: int = 50,
    ) -> dict:
        """Apollo people search tuned for recruitment candidate sourcing.

        Uses ``person_titles[]`` with ``include_similar_titles=true`` for
        semantic title matching. Industry is matched against the CURRENT
        employer's industry (Apollo has no past-industry filter). Location is
        free text — pass the country (e.g. ``"germany"``).

        Two-axis fallback:
          1. Title variants — scraped titles often include location/qualifier
             suffixes (e.g. "Head of Investment Placement - UAE") or are too
             narrow ("Head of Investment Placement"). On 0 results we strip
             trailing " - <suffix>" then token-shrink down to 3 tokens.
          2. Industry — if industry-scoped search yields 0, retry without.

        Returns a dict with:
            ``people`` (list of Apollo person dicts), ``applied_industry_fallback``
            (bool), ``applied_title_fallback`` (bool), ``title_used`` (str),
            and ``params_used`` for debugging.
        """
        empty = {
            "people": [],
            "applied_industry_fallback": False,
            "applied_title_fallback": False,
            "title_used": title or "",
            "params_used": {},
        }
        if not title:
            return empty

        variants = _title_variants(title)
        for idx, variant in enumerate(variants):
            base_params: dict = {
                "person_titles[]": [variant],
                "include_similar_titles": "true",
            }
            if location_country:
                base_params["person_locations[]"] = [location_country.strip().lower()]

            label = f"candidate search [{variant}]"

            # --- attempt 1: with industry (if provided) ---
            if current_industry:
                params = {**base_params, "person_industries[]": [current_industry]}
                people = self._paged_search_capped(params, label, max_results)
                if people:
                    return {
                        "people": people[:max_results],
                        "applied_industry_fallback": False,
                        "applied_title_fallback": idx > 0,
                        "title_used": variant,
                        "params_used": params,
                    }
                logger.info(
                    "Apollo: 0 results for title=%r industry=%r — dropping industry",
                    variant, current_industry,
                )

            # --- attempt 2: without industry (broader pool) ---
            people = self._paged_search_capped(base_params, label, max_results)
            if people:
                return {
                    "people": people[:max_results],
                    "applied_industry_fallback": bool(current_industry),
                    "applied_title_fallback": idx > 0,
                    "title_used": variant,
                    "params_used": base_params,
                }
            if idx + 1 < len(variants):
                logger.info(
                    "Apollo: 0 results for title=%r — trying shorter variant %r",
                    variant, variants[idx + 1],
                )

        return empty

    def _paged_search_capped(
        self, extra_params: dict, label: str, max_results: int,
    ) -> list[dict]:
        """Like _paged_search but stops paginating once max_results is reached.

        Apollo's per_page max is 100; for candidate sourcing we cap each search
        at 50 by default, so this is almost always a single-page call.
        """
        all_people: list[dict] = []
        page = 1
        per_page = min(APOLLO_PER_PAGE, max_results)
        while True:
            params = {"per_page": per_page, "page": page, **extra_params}
            data = self._request_page(params, label, label)
            if data is None:
                break
            people = data.get("people", [])
            all_people.extend(people)
            if len(all_people) >= max_results or not people:
                break
            page += 1
            time.sleep(0.5)
        return all_people

    # ------------------------------------------------------------------
    # Enrichment (kept for on-demand use; not invoked by orchestrator)
    # ------------------------------------------------------------------

    def enrich(self, people: list[dict]) -> list[dict]:
        enriched: list[dict] = []
        total = len(people)
        if total == 0:
            return enriched

        if total > APOLLO_BULK_BATCH_SIZE:
            cutoff = (total // APOLLO_BULK_BATCH_SIZE) * APOLLO_BULK_BATCH_SIZE
            bulk_people = people[:cutoff]
            remainder = people[cutoff:]
            for i in range(0, len(bulk_people), APOLLO_BULK_BATCH_SIZE):
                batch = bulk_people[i : i + APOLLO_BULK_BATCH_SIZE]
                enriched.extend(self._enrich_bulk(batch))
                time.sleep(0.5)
            for person in remainder:
                r = self._enrich_single(person)
                if r:
                    enriched.append(r)
                time.sleep(0.3)
        else:
            for person in people:
                r = self._enrich_single(person)
                if r:
                    enriched.append(r)
                time.sleep(0.3)
        return enriched

    def _enrich_bulk(self, people: list[dict]) -> list[dict]:
        details = [{"id": p["id"]} for p in people if p.get("id")]
        if not details:
            return []
        try:
            resp = requests.post(
                f"{APOLLO_BASE_URL}/people/bulk_match",
                headers=self._headers(),
                json={"details": details, "reveal_personal_emails": True},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("matches", [])
        except Exception as e:
            logger.error("Apollo bulk enrich error: %s", e)
            return []

    def _enrich_single(self, person: dict) -> dict | None:
        pid = person.get("id")
        if not pid:
            return None
        try:
            resp = requests.post(
                f"{APOLLO_BASE_URL}/people/match",
                headers=self._headers(),
                json={"id": pid, "reveal_personal_emails": True},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("person", {})
        except Exception as e:
            logger.error("Apollo enrich error for %s: %s", pid, e)
            return None

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def find_prospects(
        self,
        domain: str,
        industry_name: str | None,
        enrich: bool = False,
    ) -> dict[str, Any]:
        """
        Returns:
          strategy   — "primary" or "fallback"
          accepted   — prospects to keep
          rejected   — prospects rejected at any step
          stats      — counts per stage
        """
        if not domain:
            return {"strategy": "none", "accepted": [], "rejected": [], "stats": {}}

        key = normalize_industry_name(industry_name or "")
        titles = INDUSTRY_PERSONA_MAP.get(key, DEFAULT_PERSONA_TITLES)

        self._rate_limited = False

        # Step 1 — title-based search
        title_hits = self.search_by_titles(domain, titles)
        if title_hits:
            processed = self.enrich(title_hits) if enrich else title_hits
            for p in processed:
                p["_filter_step"] = "selected"
                p.setdefault("_match_reasons", ["primary_title_match"])
            return {
                "strategy": "primary",
                "accepted": processed,
                "rejected": [],
                "stats": {
                    "title_hits": len(title_hits),
                    "processed": len(processed),
                    "selected": len(processed),
                    "is_enriched": enrich,
                },
            }

        # Step 2 — fallback to seniority + filters
        logger.info("Title search empty at %s, falling back to seniority", domain)
        raw = self.search_by_seniority(domain)

        pre_filter = ProspectPreFilter(industry_name)
        pre_accepted, pre_rejected = pre_filter.filter(raw)

        processed = self.enrich(pre_accepted) if enrich else pre_accepted

        post_filter = ProspectPostFilter(industry_name)
        post_accepted, post_rejected = post_filter.extract_personas(processed)

        all_rejected = pre_rejected + post_rejected
        return {
            "strategy": "fallback",
            "accepted": post_accepted,
            "rejected": all_rejected,
            "stats": {
                "raw_hits": len(raw),
                "pre_accepted": len(pre_accepted),
                "processed": len(processed),
                "selected": len(post_accepted),
                "rejected": len(all_rejected),
                "is_enriched": enrich,
            },
        }
