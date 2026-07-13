"""
Apify LinkedIn COMPANY details service (Phase-2 industry / domain resolution).

Replaces the fragile self-hosted ``linkedin_api`` company lookup
(``linkedin_service.LinkedInCompanyService``) — which logs into LinkedIn with a
real account + residential proxy and is captcha/ban-prone — with the managed
``harvestapi/linkedin-company`` Apify actor ("no cookies", account+proxy pool,
~$0.004/company).

It returns the SAME output dict shape as ``LinkedInCompanyService.fetch_company_info``
so Phase 2 (``orchestrator.py``) consumes it unchanged:

    companyName, companyIndustries (list[str]), staffingCompany (bool),
    staffCount (int), description, companyPageUrl, companyDomain, website,
    headquarter (structured), companyLocation (display string)

Actor input (verified from its build input schema):
    { "companies": ["https://www.linkedin.com/company/<slug>", ...] }   # by URL
Actor output item (relevant fields): name, universalName, linkedinUrl, website,
    employeeCount, description, industries[{name,…}], locations[{…, headquarter}].
There is no ``staffingCompany`` field, so we derive it from the industry names.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.config import settings
from app.services.apify_profile_service import (
    ApifyEnrichmentError, ApifyNotConfigured, ApifyQuotaExceeded, ApifyRunFailed,
    _is_quota_error, _run_to_dict,
)

logger = logging.getLogger(__name__)

_COST_PER_COMPANY = 0.004  # for a cost log line only.
# LinkedIn industry names that mark a company as a staffing / recruitment agency
# (Phase 2 rejects these). The old linkedin_api gave a direct staffingCompany
# bool; the actor doesn't, so we infer it from the reported industries.
_STAFFING_MARKERS = ("staffing", "recruit", "executive search")


class ApifyCompanyService:
    """Fetch LinkedIn company details in bulk via the HarvestAPI Apify actor."""

    def __init__(self, token: Optional[str] = None, actor: Optional[str] = None) -> None:
        self._token = token or settings.APIFY_TOKEN
        self._actor = actor or settings.APIFY_COMPANY_ACTOR

    # ── client ────────────────────────────────────────────────────────────────
    def _client(self):
        if not self._token:
            raise ApifyNotConfigured("APIFY_TOKEN is not set — add it to BE/.env for company lookups.")
        try:
            from apify_client import ApifyClient
        except ImportError as exc:  # pragma: no cover
            raise ApifyEnrichmentError("apify-client is not installed — `pip install apify-client`.") from exc
        return ApifyClient(self._token)

    # ── slug / domain helpers (kept identical to LinkedInCompanyService) ───────
    @staticmethod
    def get_slug(url: str) -> Optional[str]:
        """Extract the company slug from a LinkedIn company URL."""
        if not url:
            return None
        parts = url.rstrip("/").split("/")
        try:
            idx = parts.index("company")
            return parts[idx + 1] if idx + 1 < len(parts) else None
        except ValueError:
            return None

    @staticmethod
    def extract_domain(company_url: str) -> str:
        """Return the bare domain from any URL (strips www.)."""
        if not company_url:
            return ""
        return urlparse(company_url).netloc.lower().replace("www.", "")

    # ── output mapping ─────────────────────────────────────────────────────────
    @staticmethod
    def _headquarter(item: Dict[str, Any]) -> Dict[str, Any]:
        """Pull the HQ location (``headquarter: true``) from the actor's
        ``locations[]`` and shape it like the old payload."""
        locs = item.get("locations") or []
        hq = next((l for l in locs if isinstance(l, dict) and l.get("headquarter")), None)
        if hq is None and locs and isinstance(locs[0], dict):
            hq = locs[0]  # fall back to the first location
        if not hq:
            return {}
        return {
            "city": hq.get("city") or "",
            "country": hq.get("country") or "",
            "geographicArea": hq.get("geographicArea") or "",
            "postalCode": hq.get("postalCode") or "",
            "line1": hq.get("line1") or "",
        }

    @staticmethod
    def _hq_display(hq: Dict[str, Any]) -> str:
        parts = [hq.get("city"), hq.get("geographicArea"), hq.get("country")]
        return ", ".join([p for p in parts if p]).strip(", ")

    @classmethod
    def _to_info(cls, item: Dict[str, Any]) -> Dict[str, Any]:
        """Map one actor company item → the Phase-2 info dict."""
        industries = [
            (i.get("name") or i.get("title") or "").strip()
            for i in (item.get("industries") or [])
            if isinstance(i, dict) and (i.get("name") or i.get("title"))
        ]
        website = item.get("website") or ""
        hq = cls._headquarter(item)
        staffing = any(
            any(m in ind.lower() for m in _STAFFING_MARKERS) for ind in industries
        )
        return {
            "companyName": item.get("name") or "",
            "companyIndustries": industries,
            "staffingCompany": staffing,
            "staffCount": item.get("employeeCount") or 0,
            "description": item.get("description") or "",
            "companyPageUrl": website,
            "companyDomain": cls.extract_domain(website),
            "website": website,
            "headquarter": hq,
            "companyLocation": cls._hq_display(hq),
        }

    @staticmethod
    def _result_keys(item: Dict[str, Any]) -> List[str]:
        """Slugs an item can be looked up by (lower-cased): universalName + the
        slug parsed from its linkedinUrl."""
        keys: List[str] = []
        un = item.get("universalName")
        if un:
            keys.append(str(un).lower())
        slug = ApifyCompanyService.get_slug(item.get("linkedinUrl") or "")
        if slug and slug.lower() not in keys:
            keys.append(slug.lower())
        return keys

    # ── main entry point ────────────────────────────────────────────────────────
    def fetch_companies_info(self, urls: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch several companies in one (chunked) actor run.

        Returns ``{ requested_url: info_dict }`` for every URL that resolved.
        URLs that produced no item are simply absent, so the caller can detect
        misses. Raises ``ApifyQuotaExceeded`` only if a plan block returned
        NOTHING (partial results are returned as-is).
        """
        # De-dup by slug while remembering every original URL that maps to it.
        slug_to_urls: Dict[str, List[str]] = {}
        for url in urls:
            slug = self.get_slug(url)
            if slug:
                slug_to_urls.setdefault(slug.lower(), []).append(url)
        if not slug_to_urls:
            return {}

        want_slugs = list(slug_to_urls.keys())
        # Rebuild canonical company URLs from slugs (one per unique company).
        canonical = {s: f"https://www.linkedin.com/company/{s}" for s in want_slugs}

        client = self._client()
        batch = max(1, int(settings.APIFY_ENRICH_BATCH or 10))
        by_slug: Dict[str, Dict[str, Any]] = {}
        quota_msg: Optional[str] = None
        for start in range(0, len(want_slugs), batch):
            chunk = want_slugs[start:start + batch]
            try:
                self._run_chunk([canonical[s] for s in chunk], by_slug)
            except ApifyQuotaExceeded as exc:
                quota_msg = str(exc)
                logger.warning("[ApifyCompany] plan/quota block at chunk %d: %s", start // batch, exc)
                break

        if not by_slug and quota_msg:
            raise ApifyQuotaExceeded(quota_msg)

        # Fan the per-slug info back out to every requested URL.
        results: Dict[str, Dict[str, Any]] = {}
        for slug, info in by_slug.items():
            for url in slug_to_urls.get(slug, []):
                results[url] = info

        missing = [s for s in want_slugs if s not in by_slug]
        if missing:
            logger.warning("[ApifyCompany] %d/%d companies returned no data: %s",
                           len(missing), len(want_slugs), ", ".join(missing[:10]))
        logger.info("[ApifyCompany] resolved %d/%d companies", len(by_slug), len(want_slugs))
        return results

    def _run_chunk(self, company_urls: List[str], by_slug: Dict[str, Dict[str, Any]]) -> None:
        """Run the actor for one chunk of company URLs, folding results into
        ``by_slug`` keyed by every slug they match."""
        run_input = {"companies": company_urls}
        logger.info("[ApifyCompany] fetching %d company(ies) via %s (est. $%.3f)",
                    len(company_urls), self._actor, len(company_urls) * _COST_PER_COMPANY)

        client = self._client()
        try:
            run = client.actor(self._actor).call(run_input=run_input)
        except Exception as exc:
            raise ApifyRunFailed(f"Apify company actor call failed: {exc}") from exc

        info = _run_to_dict(run)
        if info.get("status") != "SUCCEEDED":
            raise ApifyRunFailed(f"Apify company run status {info.get('status')!r} (expected SUCCEEDED).")
        dataset_id = info.get("defaultDatasetId") or info.get("default_dataset_id")
        if not dataset_id:
            raise ApifyRunFailed("Apify company run returned no defaultDatasetId.")

        n_before = len(by_slug)
        for item in client.dataset(dataset_id).iterate_items():
            if not isinstance(item, dict):
                continue
            if item.get("error") and not item.get("name"):
                if _is_quota_error(item.get("error")):
                    raise ApifyQuotaExceeded(f"Apify plan limit: {item.get('error')}")
                continue
            mapped = self._to_info(item)
            for key in self._result_keys(item):
                by_slug.setdefault(key, mapped)

        added = len(by_slug) - n_before
        try:
            from app.services import cost_service
            if added > 0:
                vendor = info.get("usageTotalUsd") or info.get("usage_total_usd")
                cost_service.record_event(
                    service="apify", operation="company_scrape", unit="company",
                    quantity=added, cost_override=(float(vendor) if vendor else None),
                    vendor_ref=str(info.get("id") or dataset_id),
                )
        except Exception:  # noqa: BLE001
            pass

    def fetch_company_info(self, url: str) -> Optional[Dict[str, Any]]:
        """Single-company convenience wrapper (mirrors the old interface)."""
        res = self.fetch_companies_info([url])
        return res.get(url)


_service: Optional[ApifyCompanyService] = None


def get_apify_company_service() -> ApifyCompanyService:
    global _service
    if _service is None:
        _service = ApifyCompanyService()
    return _service
