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
    settings,
)

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

    def search_people_by_titles(
        self, domain: str, titles: list[str], max_results: int = 50,
    ) -> list[dict]:
        """Search Apollo for people at ``domain`` matching ``titles``.

        Department-agnostic (the AI sourcing agent decides the relevant titles,
        which may belong to any function), with ``include_similar_titles`` for
        semantic matching. Free people-search (no enrichment credits).
        """
        if not domain or not titles:
            return []
        self._rate_limited = False
        logger.info("AI title search: %s at %s", titles[:3], domain)
        return self._paged_search_capped(
            {
                "person_titles[]": titles,
                "include_similar_titles": "true",
                "q_organization_domains_list[]": [domain],
            },
            f"ai title search {titles[:2]}",
            max_results,
        )

    def search_all_people(self, domain: str, max_results: int = 50) -> list[dict]:
        """Plain people-search by company domain only — NO title/seniority filter.

        Used as the fallback when targeted title searches return nobody (common
        for small startups whose titles don't match standard leadership labels).
        Returns the full roster so the AI can pick the real decision-makers.
        """
        if not domain:
            return []
        self._rate_limited = False
        logger.info("AI plain company search at %s", domain)
        return self._paged_search_capped(
            {"q_organization_domains_list[]": [domain]},
            f"ai plain search {domain}",
            max_results,
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

    def search_people(
        self,
        *,
        titles: list[str] | None = None,
        locations: list[str] | None = None,
        skills: list[str] | None = None,
        seniorities: list[str] | None = None,
        industries: list[str] | None = None,
        max_results: int = 50,
    ) -> dict:
        """Questionnaire-driven people search for the Apollo discovery flow.

        Unlike ``search_candidates`` (single title + industry-fallback cascade),
        this takes the recruiter's structured filters straight from the Apollo
        questionnaire and ANDs them together in one search:

          * titles       → ``person_titles[]`` (+ ``include_similar_titles``)
          * locations     → ``person_locations[]`` (free text, lower-cased)
          * seniorities  → ``person_seniorities[]`` (Apollo enum codes)
          * industries   → ``person_industries[]``
          * skills       → ``q_keywords`` — Apollo has NO structured skills
                           filter, so key skills are matched as free text across
                           the profile (a soft match, not a hard requirement).

        Free people-search: no enrichment credits are consumed and contact info
        (email/phone) comes back masked — reveal it later per-candidate via
        ``/people/match``. Returns ``{"people": [...], "params_used": {...}}``.

        FALLBACK CASCADE — mirrors the Apify Broadener's intent so an over-narrow
        filter set doesn't just return zero. We relax, widest-signal-last, and
        stop at the first attempt that returns anyone:
          1. everything (titles + q_keywords + seniorities + locations)
          2. drop q_keywords     (skills AND-narrow the hardest — case study §B1)
          3. drop seniorities    (a title family already carries seniority signal)
          4. drop locations      (country/geo may be mis-parsed; title is the anchor)

        NOTE: ``industries`` no longer maps to ``person_industries[]`` — that is
        not a documented Apollo people-search param and was silently ignored
        (case study §B2). Any industry terms are folded into ``q_keywords``.
        """
        self._rate_limited = False
        clean = lambda xs: [x.strip() for x in (xs or []) if x and x.strip()]  # noqa: E731

        base_titles = clean(titles)
        base_locations = [x.lower() for x in clean(locations)]
        base_seniorities = clean(seniorities)
        # Skills + industries both become free-text q_keywords (Apollo has no
        # structured skills filter, and person_industries[] is not real).
        keyword_terms = clean(skills) + clean(industries)
        base_keywords = " ".join(keyword_terms)

        if not base_titles and not base_keywords:
            return {"people": [], "params_used": {}}

        def _params(*, with_keywords: bool, with_seniorities: bool,
                    with_locations: bool) -> dict:
            p: dict = {}
            if base_titles:
                p["person_titles[]"] = base_titles
                p["include_similar_titles"] = "true"
            if with_locations and base_locations:
                p["person_locations[]"] = base_locations
            if with_seniorities and base_seniorities:
                p["person_seniorities[]"] = base_seniorities
            if with_keywords and base_keywords:
                p["q_keywords"] = base_keywords
            return p

        # Each stage is strictly broader than the one before it.
        stages = [
            _params(with_keywords=True, with_seniorities=True, with_locations=True),
            _params(with_keywords=False, with_seniorities=True, with_locations=True),
            _params(with_keywords=False, with_seniorities=False, with_locations=True),
            _params(with_keywords=False, with_seniorities=False, with_locations=False),
        ]

        last_params: dict = stages[0]
        seen: list[dict] = []
        for idx, params in enumerate(stages):
            if not params or params in seen:
                continue  # nothing left to search on, or identical to a prior try
            seen.append(params)
            last_params = params
            logger.info("Apollo questionnaire search (stage %d): %s", idx, params)
            people = self._paged_search_capped(
                params, f"apollo questionnaire search s{idx}", max_results,
            )
            if people:
                return {
                    "people": people[:max_results],
                    "params_used": params,
                    "applied_fallback_stage": idx,
                }
            if self._rate_limited:
                # Throttled — a broader stage would 429 too; stop here.
                break
        return {"people": [], "params_used": last_params, "applied_fallback_stage": None}

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

    def match_phone(
        self,
        *,
        apollo_id: str | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        organization_name: str | None = None,
        webhook_url: str = "",
    ) -> dict | None:
        """Apollo /people/match with reveal_phone_number=True (credit-consuming).

        Apollo may return the phone immediately (cached) or deliver it later to
        ``webhook_url``. Returns the matched person dict (which may already carry
        ``phone_numbers``) or None on failure.
        """
        body: dict = {"reveal_phone_number": True, "reveal_personal_emails": False}
        if webhook_url:
            body["webhook_url"] = webhook_url
        if apollo_id:
            body["id"] = apollo_id
        if email:
            body["email"] = email
        if first_name:
            body["first_name"] = first_name
        if last_name:
            body["last_name"] = last_name
        if organization_name:
            body["organization_name"] = organization_name
        try:
            resp = requests.post(
                f"{APOLLO_BASE_URL}/people/match",
                headers=self._headers(),
                json=body,
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json().get("person", {})
        except Exception as e:
            logger.error("Apollo phone match error: %s", e)
            return None

    def match_person(
        self,
        *,
        linkedin_url: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        organization_name: str | None = None,
        email: str | None = None,
    ) -> dict | None:
        """Resolve a person to their Apollo person id via /people/match (no reveal).

        This is the "people search" step for phone enrichment. Candidates sourced
        from LinkedIn/Apify carry a LinkedIn URN in ``apolloId`` — NOT a real Apollo
        person id — so we must resolve the real id (from the LinkedIn URL, or
        name + company) before a phone reveal can target them. No ``reveal_*`` flags
        are set, so this does not unlock email/phone; it only returns the match
        (whose ``id`` is the Apollo person id). Returns the person dict or None.
        """
        body: dict = {"reveal_personal_emails": False, "reveal_phone_number": False}
        if linkedin_url:
            body["linkedin_url"] = linkedin_url
        if email:
            body["email"] = email
        if first_name:
            body["first_name"] = first_name
        if last_name:
            body["last_name"] = last_name
        if organization_name:
            body["organization_name"] = organization_name
        try:
            resp = requests.post(
                f"{APOLLO_BASE_URL}/people/match",
                headers=self._headers(),
                json=body,
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json().get("person", {})
        except Exception as e:
            logger.error("Apollo person match error: %s", e)
            return None

    @staticmethod
    def extract_mobile(person: dict) -> str | None:
        """Pull the best mobile/phone number from an Apollo person payload."""
        phones = person.get("phone_numbers") or []
        if not phones:
            return None
        mobiles = [p for p in phones if isinstance(p, dict) and (p.get("type") == "mobile" or p.get("type_cd") == "mobile")]
        pick = (mobiles or phones)[0]
        if isinstance(pick, dict):
            return pick.get("sanitized_number") or pick.get("raw_number")
        return pick if isinstance(pick, str) else None

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

    # Prospect sourcing is now handled by the AI agent in
    # app/services/agent/prospect_sourcing_agent.py (no static title lists / no
    # keyword accept-reject filters). This service only exposes the raw Apollo
    # search/enrich primitives the agent uses.
