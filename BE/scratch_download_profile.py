"""
Standalone runner for the LinkedIn profile download service.

Usage (from BE/):
    python scratch_download_profile.py https://www.linkedin.com/in/satyanadella/
    python scratch_download_profile.py satyanadella          # bare slug works too

Exercises the full Step-1 flow end to end against the live LinkedIn session:
extract slug -> fetch modern dash profile -> structure -> print JSON.

If LinkedIn is throttling this IP (no residential proxy), it prints a clear
"throttled" message instead of a stack trace — set LINKEDIN_PROXY_URL or wait
for a cooldown.
"""

import json
import logging
import sys

from linkedin_api import Linkedin

from app.config import settings
from app.services.linkedin_profile_service import (
    LinkedInProfileService,
    LinkedInThrottled,
    extract_profile_slug,
    get_linkedin_profile_service,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Auth mode for local testing:
#   "cookies"  → use the fresh li_at / JSESSIONID from .env (cookie injection).
#                Use this right after pasting new browser cookies.
#   "password" → do a FRESH password login every run (no cached jar, so it can
#                never reuse a poisoned session). Slower, can be challenged.
AUTH_MODE = "cookies"


def _build_service() -> LinkedInProfileService:
    if AUTH_MODE == "password" and settings.LINKEDIN_EMAIL and settings.LINKEDIN_PASSWORD:
        # refresh_cookies=True + no cookies_dir → always a clean login, never a
        # stale cache (that stale-cache reuse was the earlier 401 trap).
        api = Linkedin(settings.LINKEDIN_EMAIL, settings.LINKEDIN_PASSWORD, refresh_cookies=True)
        return LinkedInProfileService(api=api)
    # "cookies": cookie injection straight from .env (honours freshly pasted cookies)
    return get_linkedin_profile_service()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    url = sys.argv[1].strip()
    slug = extract_profile_slug(url)
    if not slug:
        print(f"[x] Could not extract a LinkedIn slug from: {url}")
        return 1
    print(f"-> slug: {slug}")

    try:
        service = _build_service()
        profile = service.download_profile(url)
    except LinkedInThrottled as exc:
        print(f"\n[!] THROTTLED: {exc}")
        return 3

    if profile is None:
        print("\n[x] No profile returned (private profile, expired session, or empty payload).")
        return 1

    print("\n[ok] Profile downloaded:\n")
    print(json.dumps(profile, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
