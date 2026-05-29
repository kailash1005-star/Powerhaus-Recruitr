"""
LinkedIn Company Info Service
Fetch company details from LinkedIn API, extract slug/domain, return structured info.

Session management
------------------
LinkedIn challenges datacenter IPs with captchas and bans accounts that log in
repeatedly. To stay alive in production we:

  1. Log in **once** and reuse the same authenticated `Linkedin` session for every
     company lookup (process-wide singleton — see `get_linkedin_service()`).
  2. Persist the session cookie jar to disk (`LINKEDIN_COOKIE_DIR`) so even across
     process restarts / redeploys a still-valid session is reused without a fresh
     login. linkedin_api validates the cached JSESSIONID expiry automatically.
  3. Route all traffic through a residential proxy (`LINKEDIN_PROXY_URL`) when set,
     because LinkedIn captchas datacenter IPs.
"""

import logging
import os
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from linkedin_api import Linkedin
from linkedin_api.cookie_repository import LinkedinSessionExpired
from linkedin_api.client import ChallengeException, UnauthorizedException
from requests.cookies import RequestsCookieJar

from app.config import settings

logger = logging.getLogger(__name__)


class LinkedInCompanyService:
    """Wraps linkedin_api to fetch and normalise company information.

    Authenticates once on construction (reusing cached cookies when possible) and
    keeps the authenticated session for the lifetime of the instance.
    """

    def __init__(self, api: Linkedin | None = None) -> None:
        if api is not None:
            self._api = api
        else:
            self._api = self._build_api()

    @staticmethod
    def _build_api() -> Linkedin:
        """Create an authenticated Linkedin client, reusing cached cookies if valid.

        linkedin_api treats ``cookies_dir`` as a string *prefix* when building the
        cookie-jar filename, so it must end with a path separator.
        """
        cookie_dir = settings.LINKEDIN_COOKIE_DIR or ".linkedin_cookies/"
        if not cookie_dir.endswith(("/", os.sep)):
            cookie_dir += os.sep
        os.makedirs(cookie_dir, exist_ok=True)

        proxies: dict[str, str] = {}
        if settings.LINKEDIN_PROXY_URL:
            proxies = {
                "http": settings.LINKEDIN_PROXY_URL,
                "https": settings.LINKEDIN_PROXY_URL,
            }
            logger.info("[LinkedIn] Routing requests through residential proxy")

        email = settings.LINKEDIN_EMAIL
        password = settings.LINKEDIN_PASSWORD

        # Preferred path: inject a real browser session via cookies. This skips the
        # username/password login entirely, which is what LinkedIn CHALLENGEs.
        if settings.LINKEDIN_LI_AT and settings.LINKEDIN_JSESSIONID:
            jsessionid = settings.LINKEDIN_JSESSIONID
            if not jsessionid.startswith('"'):
                jsessionid = f'"{jsessionid}"'  # library strips the surrounding quotes
            jar = RequestsCookieJar()
            jar.set("li_at", settings.LINKEDIN_LI_AT, domain=".linkedin.com", path="/")
            jar.set("JSESSIONID", jsessionid, domain=".linkedin.com", path="/")
            api = Linkedin(email, password, proxies=proxies, cookies=jar)
            logger.info("[LinkedIn] Session ready (injected browser cookies — no password login)")
            return api

        try:
            api = Linkedin(
                email,
                password,
                cookies_dir=cookie_dir,
                proxies=proxies,
                refresh_cookies=False,
            )
            logger.info("[LinkedIn] Session ready (reused cached cookies if available)")
            return api
        except LinkedinSessionExpired:
            # Cached cookies exist but expired → force a fresh login (overwrites jar).
            logger.warning("[LinkedIn] Cached session expired — performing fresh login")
            api = Linkedin(
                email,
                password,
                cookies_dir=cookie_dir,
                proxies=proxies,
                refresh_cookies=True,
            )
            logger.info("[LinkedIn] Fresh session established and cached")
            return api
        except ChallengeException as exc:
            logger.error(
                "[LinkedIn] Login challenged (captcha) — a residential IP is required "
                "for unattended login. Detail: %s", exc,
            )
            raise
        except UnauthorizedException as exc:
            logger.error("[LinkedIn] Login unauthorized — check LINKEDIN_EMAIL/PASSWORD: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Slug / domain helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_slug(url: str) -> Optional[str]:
        """Extract company slug from a LinkedIn URL."""
        if not url:
            return None
        url = url.rstrip("/")
        parts = url.split("/")
        try:
            idx = parts.index("company")
            return parts[idx + 1] if idx + 1 < len(parts) else None
        except ValueError:
            return None

    @staticmethod
    def extract_domain(company_url: str) -> str:
        """Return bare domain from any URL (strips www.)."""
        if not company_url:
            return ""
        netloc = urlparse(company_url).netloc.lower().replace("www.", "")
        return netloc

    # ------------------------------------------------------------------
    # LinkedIn API calls
    # ------------------------------------------------------------------

    def fetch_company_raw(self, url: str) -> Optional[Dict[str, Any]]:
        """Call LinkedIn API and return the raw company dict (or None)."""
        slug = self.get_slug(url)
        if not slug:
            return None
        try:
            return self._api.get_company(slug)
        except Exception as exc:
            logger.warning("[LinkedIn] Error fetching %s: %s", slug, exc)
            return None

    def fetch_company_info(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Fetch company from LinkedIn and return only the fields we need:
          companyIndustries, staffingCompany, staffCount,
          description, companyPageUrl, companyName, website
        """
        data = self.fetch_company_raw(url)
        if data is None:
            return None

        industries: list[str] = []
        for ind in data.get("companyIndustries", []):
            name = ind.get("localizedName", "")
            if name:
                industries.append(name)

        website = data.get("companyPageUrl", "")
        domain = self.extract_domain(website)

        return {
            "companyName": data.get("name") or "",
            "companyIndustries": industries,
            "staffingCompany": data.get("staffingCompany", False),
            "staffCount": data.get("staffCount", 0),
            "description": data.get("description", ""),
            "companyPageUrl": website,
            "companyDomain": domain,
            "website": data.get("url", ""),
        }


# ---------------------------------------------------------------------------
# Process-wide singleton — log in once, reuse the session everywhere
# ---------------------------------------------------------------------------

_service_singleton: "LinkedInCompanyService | None" = None
_singleton_lock = threading.Lock()


def get_linkedin_service() -> "LinkedInCompanyService":
    """Return a shared, authenticated LinkedInCompanyService.

    The first call authenticates (or loads cached cookies); every subsequent call
    reuses the same session. Thread-safe so concurrent workers don't each trigger
    a login.
    """
    global _service_singleton
    if _service_singleton is None:
        with _singleton_lock:
            if _service_singleton is None:
                _service_singleton = LinkedInCompanyService()
    return _service_singleton
