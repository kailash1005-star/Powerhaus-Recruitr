"""
LinkedIn Profile Download Service
Downloads a candidate's full profile from LinkedIn given a profile URL.

Step 1 of the candidate sourcing pipeline: given a LinkedIn URL, fetch the
complete profile and return clean, structured JSON (identity, experience,
education, skills).

────────────────────────────────────────────────────────────────────────────
Why this is NOT the obvious `linkedin_api.get_profile`
────────────────────────────────────────────────────────────────────────────
LinkedIn retired the classic Voyager profile endpoints. Verified live (2026-06)
against an authenticated session:

  • GET /identity/profiles/{slug}/profileView   → 410 Gone   (what
    linkedin_api.get_profile / get_profile_contact_info / networkinfo all use)
  • GET /identity/profiles/{slug}/profilePdf    → 406        (never real)

The live replacement is the modern **`/identity/dash/*`** API — the same one the
LinkedIn web app calls. Verified working endpoints + shapes:

  core       GET /identity/dash/profiles?q=memberIdentity&memberIdentity={slug}
  experience GET /identity/dash/profilePositions?q=viewee&profileUrn={urn}
  education  GET /identity/dash/profileEducations?q=viewee&profileUrn={urn}
  skills     GET /identity/dash/profileSkills?q=viewee&profileUrn={urn}

The core call returns ONLY the Profile entity (name/headline/summary + URN); the
three sections live behind their own endpoints keyed by the profile URN, so a
full profile is 1 core call + 3 section calls.

────────────────────────────────────────────────────────────────────────────
Anti-bot / throttling
────────────────────────────────────────────────────────────────────────────
LinkedIn rate-limits this Voyager API aggressively: a short burst of requests
triggers a self-redirect (302) that tries to delete the `li_at` cookie (a silent
logout) and locks the session for minutes. We:

  • send requests with `allow_redirects=False` and treat 302 / 429 / 999 as a
    throttle signal — never follow the bounce (it just loops 30×);
  • space requests out (`_MIN_REQUEST_INTERVAL`) and retry with exponential
    backoff + jitter;
  • surface a typed `LinkedInThrottled` so callers can degrade gracefully.

The session is a process-wide singleton so we authenticate once and reuse one
warm session for every lookup. For bulk runs, space profiles out generously.
"""

import logging
import os
import random
import re
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from linkedin_api import Linkedin
from linkedin_api.client import ChallengeException, UnauthorizedException
from linkedin_api.cookie_repository import LinkedinSessionExpired
from requests.cookies import RequestsCookieJar

from app.config import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────────────

class LinkedInProfileError(Exception):
    """Base error for the profile service."""


class LinkedInThrottled(LinkedInProfileError):
    """LinkedIn is rate-limiting / bot-blocking this session+IP.

    Raised when the Voyager API answers with a logout-redirect (302 deleting
    li_at), 429, or 999. The cure is a fresh session and/or a cooldown.
    """


# ──────────────────────────────────────────────────────────────────────────────
# Shared LinkedIn client (singleton, reused across company + profile services)
# ──────────────────────────────────────────────────────────────────────────────

_api_singleton: "Linkedin | None" = None
_api_lock = threading.Lock()


def _build_linkedin_api() -> Linkedin:
    """Create an authenticated Linkedin client, reusing cached cookies if valid.

    Same proven auth logic as LinkedInCompanyService._build_api, so the company
    service and profile service share one warm session.
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
        logger.info("[LinkedIn Profile] Routing requests through proxy")

    email = settings.LINKEDIN_EMAIL
    password = settings.LINKEDIN_PASSWORD

    # Preferred: inject browser cookies → skip password login entirely.
    if settings.LINKEDIN_LI_AT and settings.LINKEDIN_JSESSIONID:
        jsessionid = settings.LINKEDIN_JSESSIONID
        if not jsessionid.startswith('"'):
            jsessionid = f'"{jsessionid}"'
        jar = RequestsCookieJar()
        jar.set("li_at", settings.LINKEDIN_LI_AT, domain=".linkedin.com", path="/")
        jar.set("JSESSIONID", jsessionid, domain=".linkedin.com", path="/")
        api = Linkedin(email, password, proxies=proxies, cookies=jar)
        logger.info("[LinkedIn Profile] Session ready (injected browser cookies)")
        return api

    # Fallback: email/password with cookie caching (auto-refreshes on expiry).
    try:
        api = Linkedin(email, password, cookies_dir=cookie_dir, proxies=proxies, refresh_cookies=False)
        logger.info("[LinkedIn Profile] Session ready (cached cookies)")
        return api
    except LinkedinSessionExpired:
        logger.warning("[LinkedIn Profile] Cached session expired — fresh login")
        api = Linkedin(email, password, cookies_dir=cookie_dir, proxies=proxies, refresh_cookies=True)
        logger.info("[LinkedIn Profile] Fresh session established")
        return api
    except ChallengeException as exc:
        logger.error("[LinkedIn Profile] Login challenged (captcha): %s", exc)
        raise
    except UnauthorizedException as exc:
        logger.error("[LinkedIn Profile] Login unauthorized: %s", exc)
        raise


def get_linkedin_api() -> Linkedin:
    """Return a process-wide authenticated Linkedin client (thread-safe singleton)."""
    global _api_singleton
    if _api_singleton is None:
        with _api_lock:
            if _api_singleton is None:
                _api_singleton = _build_linkedin_api()
    return _api_singleton


# ──────────────────────────────────────────────────────────────────────────────
# URL parsing
# ──────────────────────────────────────────────────────────────────────────────

_PROFILE_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#]+)")


def extract_profile_slug(linkedin_url: str) -> Optional[str]:
    """Extract the public profile slug from a LinkedIn URL.

    Supports:
      - https://www.linkedin.com/in/satyanadella/
      - https://linkedin.com/in/satyanadella?foo=bar
      - satyanadella  (bare slug pass-through)
    """
    if not linkedin_url:
        return None
    url = linkedin_url.strip()

    # Bare slug (no slashes, no dots → not a URL)
    if "/" not in url and "." not in url:
        return url

    m = _PROFILE_SLUG_RE.search(url)
    if m:
        return m.group(1).rstrip("/")

    logger.warning("[LinkedIn Profile] Could not extract slug from: %s", url)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Profile download service
# ──────────────────────────────────────────────────────────────────────────────

_VOYAGER_BASE = "https://www.linkedin.com/voyager/api"

# Request the "normalized" response shape: a flat `included[]` of typed entities
# plus a `data` envelope. Each related entity is a flat record with a `$type`
# discriminator, which is easy and drift-resistant to parse.
_NORMALIZED_ACCEPT = "application/vnd.linkedin.normalized+json+2.1"

# Minimum spacing between Voyager calls (seconds). LinkedIn rate-limits this API
# hard: at ~2s spacing a 4-call profile gets locked partway (the last section,
# skills, is usually the casualty). Wider gaps = prevention. ~8s lands all four
# on a healthy account; if the account is already in a penalty window it still
# needs a cooldown first. Tune down only behind a proxy/account-pool.
_MIN_REQUEST_INTERVAL = 8.0

# On a throttle (302), retrying within a few seconds just extends the penalty.
# Back off LONGER between throttle retries so the window has a real chance to
# clear, and keep the retry count low so we don't hammer.
_MAX_RETRIES = 3
_THROTTLE_BACKOFF_BASE = 8.0  # seconds: ~8s, ~16s between throttle retries


class LinkedInProfileService:
    """Download and structure a candidate's LinkedIn profile.

    Public surface:
      • download_profile(url)  → full structured dict (or None on failure)

    Internals are split one-method-per-concern so each piece can be exercised
    and verified independently:
      • _voyager_get(path)          → throttle-aware HTTP GET
      • _fetch_core(slug)           → modern dash Profile entity
      • _fetch_section(...)         → positions / educations / skills
      • _parse_identity/_experience/_education/_skills → normalise to clean dicts
    """

    def __init__(self, api: Linkedin | None = None) -> None:
        self._api = api or get_linkedin_api()
        self._last_request_ts = 0.0

    # ── Low-level HTTP: throttle-aware Voyager GET ───────────────────────────

    def _voyager_get(self, path: str, *, normalized: bool = True) -> Optional[Dict[str, Any]]:
        """GET a Voyager API path and return parsed JSON, or None on a hard miss.

        Robustness rules learned from live testing:
          • allow_redirects=False — a 302 here is LinkedIn's logout-bounce, NOT a
            real redirect; following it loops 30× and burns the session.
          • 302 / 429 / 999 ⇒ throttled ⇒ backoff + retry, then raise
            LinkedInThrottled if it never clears.
          • 410 ⇒ endpoint is dead (deprecated) ⇒ return None (caller degrades).
          • 200 with empty/non-JSON body ⇒ treat as miss.
        """
        session = self._api.client.session
        headers = {"x-restli-protocol-version": "2.0.0"}
        if normalized:
            headers["accept"] = _NORMALIZED_ACCEPT

        url = f"{_VOYAGER_BASE}{path}"

        for attempt in range(1, _MAX_RETRIES + 1):
            self._respect_rate_limit()
            try:
                resp = session.get(url, headers=headers, timeout=30, allow_redirects=False)
            except Exception as exc:  # network error
                logger.warning("[LinkedIn Profile] GET %s failed (attempt %d): %s", path, attempt, exc)
                self._backoff(attempt)
                continue

            status = resp.status_code

            if status == 200:
                if not resp.content:
                    logger.warning("[LinkedIn Profile] 200 but empty body: %s", path)
                    return None
                try:
                    return resp.json()
                except ValueError:
                    logger.warning("[LinkedIn Profile] 200 but non-JSON body: %s", path)
                    return None

            if status in (302, 429, 999):
                logger.warning(
                    "[LinkedIn Profile] Throttle signal %d on %s (attempt %d/%d)",
                    status, path, attempt, _MAX_RETRIES,
                )
                if attempt < _MAX_RETRIES:
                    self._backoff(attempt, base=_THROTTLE_BACKOFF_BASE)
                    continue
                raise LinkedInThrottled(
                    f"LinkedIn throttled the session (HTTP {status}) on {path}. "
                    "Re-authenticate with a fresh session or wait for a cooldown."
                )

            if status == 410:
                logger.info("[LinkedIn Profile] Endpoint gone (410, deprecated): %s", path)
                return None

            logger.warning("[LinkedIn Profile] Unexpected %d on %s", status, path)
            return None

        return None

    def _respect_rate_limit(self) -> None:
        """Sleep just enough to keep at least _MIN_REQUEST_INTERVAL between calls."""
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        self._last_request_ts = time.monotonic()

    @staticmethod
    def _backoff(attempt: int, base: float = 2.0) -> None:
        """Linear-scaled backoff with jitter: delay = base * attempt (+ jitter).

        Network errors use base=2 (~2s, 4s). Throttles pass base=8 (~8s, 16s) so
        the penalty window has a real chance to clear instead of being
        re-triggered by an eager retry.
        """
        delay = base * attempt + random.uniform(0, 1)
        logger.info("[LinkedIn Profile] Backing off %.1fs", delay)
        time.sleep(delay)

    # ── Normalized-response helpers ──────────────────────────────────────────

    @staticmethod
    def _included(payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The flat list of typed entities from a normalized Voyager response."""
        if not payload:
            return []
        inc = payload.get("included")
        return inc if isinstance(inc, list) else []

    @staticmethod
    def _of_type(included: List[Dict[str, Any]], type_suffix: str) -> List[Dict[str, Any]]:
        """Filter included[] by the tail of the `$type` discriminator.

        e.g. type_suffix='profile.Position' matches
        'com.linkedin.voyager.dash.identity.profile.Position'.
        """
        return [e for e in included if str(e.get("$type", "")).endswith(type_suffix)]

    @staticmethod
    def _profile_entity(included: List[Dict[str, Any]]) -> Dict[str, Any]:
        """The Profile entity (carries name/headline/summary/location/URN)."""
        for e in included:
            if str(e.get("$type", "")).endswith("identity.profile.Profile") and e.get("firstName") is not None:
                return e
        for e in included:  # fallback: anything that looks like a profile
            if e.get("firstName") is not None and e.get("lastName") is not None:
                return e
        return {}

    @staticmethod
    def _ml(entity: Dict[str, Any], key: str) -> str:
        """Pull a multiLocale field (e.g. {'en_US': 'Acme'}) → 'Acme'.

        Prefers en_US, then any locale; ignores the '$type' discriminator key.
        """
        v = entity.get(key)
        if isinstance(v, dict):
            if isinstance(v.get("en_US"), str):
                return v["en_US"]
            for k, val in v.items():
                if k != "$type" and isinstance(val, str):
                    return val
        return ""

    # ── Fetch: core + sections ───────────────────────────────────────────────

    def _fetch_core(self, slug: str) -> Optional[Dict[str, Any]]:
        """Fetch the modern dash Profile payload for a public slug (verified 200)."""
        path = f"/identity/dash/profiles?q=memberIdentity&memberIdentity={slug}"
        return self._voyager_get(path, normalized=True)

    def _fetch_section(self, endpoint: str, urn_id: str, type_suffix: str) -> List[Dict[str, Any]]:
        """Fetch one profile section keyed by the profile URN.

        Best-effort: a throttle on a section degrades that section to [] rather
        than failing the whole profile (and logs it).
        """
        if not urn_id:
            return []
        eu = quote(f"urn:li:fsd_profile:{urn_id}", safe="")
        path = f"/identity/dash/{endpoint}?q=viewee&profileUrn={eu}"
        try:
            payload = self._voyager_get(path, normalized=True)
        except LinkedInThrottled as exc:
            logger.warning("[LinkedIn Profile] Section %s throttled: %s", endpoint, exc)
            return []
        return self._of_type(self._included(payload), type_suffix)

    # ── Public: download a full profile ──────────────────────────────────────

    def download_profile(self, linkedin_url: str) -> Optional[Dict[str, Any]]:
        """Download a complete LinkedIn profile given a URL or slug.

        Returns a cleaned, structured dict (identity, experience, education,
        skills) or None on failure. Raises LinkedInThrottled if the core fetch
        is rate-limited and a cooldown / fresh session is required.
        """
        slug = extract_profile_slug(linkedin_url)
        if not slug:
            logger.error("[LinkedIn Profile] Invalid URL: %s", linkedin_url)
            return None

        logger.info("[LinkedIn Profile] Downloading profile: %s", slug)
        core = self._fetch_core(slug)
        profile_entity = self._profile_entity(self._included(core))
        if not profile_entity:
            logger.warning("[LinkedIn Profile] No Profile entity for: %s", slug)
            return None

        urn_id = (profile_entity.get("entityUrn", "") or "").split(":")[-1]
        # One call per section (LinkedIn has no single "everything" endpoint).
        # Ordered most-important-first: if the account throttles partway, the
        # sections most likely to matter are already in hand.
        positions = self._fetch_section("profilePositions", urn_id, "profile.Position")
        educations = self._fetch_section("profileEducations", urn_id, "profile.Education")
        skills = self._fetch_section("profileSkills", urn_id, "profile.Skill")
        certifications = self._fetch_section("profileCertifications", urn_id, "profile.Certification")
        languages = self._fetch_section("profileLanguages", urn_id, "profile.Language")

        profile = self._structure_profile(
            profile_entity, positions, educations, skills,
            certifications, languages, linkedin_url, slug,
        )
        logger.info(
            "[LinkedIn Profile] Downloaded: %s (%s) — %d exp, %d edu, %d skills, %d certs, %d langs",
            slug, profile.get("full_name", "?"),
            len(profile["experience"]), len(profile["education"]), len(profile["skills"]),
            len(profile["certifications"]), len(profile["languages"]),
        )
        return profile

    # ── Section parsers (each independently testable) ────────────────────────

    def _parse_identity(self, profile: Dict[str, Any], slug: str) -> Dict[str, Any]:
        """Name, headline, summary, location, identifiers from the Profile entity."""
        first_name = profile.get("firstName", "") or ""
        last_name = profile.get("lastName", "") or ""
        loc = profile.get("location")
        country_code = loc.get("countryCode", "") if isinstance(loc, dict) else ""
        return {
            "full_name": f"{first_name} {last_name}".strip(),
            "first_name": first_name,
            "last_name": last_name,
            "headline": profile.get("headline", "") or self._ml(profile, "multiLocaleHeadline"),
            "summary": profile.get("summary", "") or self._ml(profile, "multiLocaleSummary"),
            # The bare core call only resolves the country code; the full
            # "City, State, Country" string lives behind the geoLocation URN
            # (separate lookup — intentionally skipped to save a request).
            "location": profile.get("locationName") or country_code or "",
            "country_code": country_code,
            "public_id": profile.get("publicIdentifier", slug),
            "profile_urn": profile.get("entityUrn", "") or "",
        }

    def _parse_experience(self, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Position entities → list of {title, company, location, dates, desc}."""
        out: List[Dict[str, Any]] = []
        for pos in positions:
            out.append({
                "title": pos.get("title", "") or self._ml(pos, "multiLocaleTitle"),
                "company_name": pos.get("companyName", "") or self._ml(pos, "multiLocaleCompanyName"),
                "location": pos.get("locationName", "") or pos.get("geoLocationName", "") or "",
                "description": pos.get("description", "") or self._ml(pos, "multiLocaleDescription"),
                "starts_at": _dash_date(pos.get("dateRange"), "start"),
                "ends_at": _dash_date(pos.get("dateRange"), "end"),
            })
        return out

    def _parse_education(self, educations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Education entities → list of {school, degree, field, grade, dates}."""
        out: List[Dict[str, Any]] = []
        for edu in educations:
            out.append({
                "school_name": edu.get("schoolName", "") or self._ml(edu, "multiLocaleSchoolName"),
                "degree_name": edu.get("degreeName", "") or self._ml(edu, "multiLocaleDegreeName"),
                "field_of_study": edu.get("fieldOfStudy", "") or self._ml(edu, "multiLocaleFieldOfStudy"),
                "grade": edu.get("grade", "") or self._ml(edu, "multiLocaleGrade"),
                "description": edu.get("description", "") or self._ml(edu, "multiLocaleDescription"),
                "starts_at": _dash_date(edu.get("dateRange"), "start"),
                "ends_at": _dash_date(edu.get("dateRange"), "end"),
            })
        return out

    def _parse_skills(self, skills: List[Dict[str, Any]]) -> List[str]:
        """Skill entities → list of skill names."""
        names: List[str] = []
        for sk in skills:
            name = sk.get("name", "") or self._ml(sk, "multiLocaleName")
            if name:
                names.append(name)
        return names

    def _parse_languages(self, languages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Language entities → [{name, proficiency}].

        `proficiency` is a LinkedIn enum (e.g. PROFESSIONAL_WORKING,
        NATIVE_OR_BILINGUAL) — humanised to 'Professional Working' to match the
        PDF. Field name is checked defensively across likely keys.
        """
        out: List[Dict[str, Any]] = []
        for lang in languages:
            name = lang.get("name", "") or self._ml(lang, "multiLocaleName")
            prof = (
                lang.get("proficiency")
                or lang.get("proficiencyName")
                or lang.get("languageProficiency")
                or ""
            )
            out.append({"name": name, "proficiency": _humanize_enum(prof)})
        return out

    def _parse_certifications(self, certifications: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Certification entities → [{name, authority, url, issued_at}].

        Field names checked defensively (authority/company, url/certificateUrl,
        issued date via dateRange or issued* keys).
        """
        out: List[Dict[str, Any]] = []
        for cert in certifications:
            issued = _dash_date(cert.get("dateRange"), "start") or _dash_date(cert.get("issuedOn"), "start")
            if not issued and isinstance(cert.get("issuedOn"), dict):
                issued = _format_date_dict(cert["issuedOn"])
            out.append({
                "name": cert.get("name", "") or self._ml(cert, "multiLocaleName"),
                "authority": (
                    cert.get("authority", "")
                    or cert.get("companyName", "")
                    or self._ml(cert, "multiLocaleAuthority")
                ),
                "url": cert.get("url", "") or cert.get("certificateUrl", ""),
                "license_number": cert.get("licenseNumber", "") or "",
                "issued_at": issued,
            })
        return out

    def _parse_contact(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        """Contact block from the core Profile entity.

        `websites` is embedded in the core response (verified); email/phone are
        usually null (LinkedIn privacy-gates them for non-connections).
        """
        websites = []
        for w in (profile.get("websites") or []):
            if isinstance(w, dict) and w.get("url"):
                websites.append({
                    "category": (w.get("category") or "").replace("_", " ").title(),
                    "url": w["url"],
                })
        phones = []
        for p in (profile.get("phoneNumbers") or []):
            if isinstance(p, dict):
                num = p.get("number") or (p.get("phoneNumber") or {}).get("number")
                if num:
                    phones.append(num)
        return {
            "email": profile.get("emailAddress") or "",
            "phone_numbers": phones,
            "websites": websites,
            "twitter": [t.get("name", str(t)) if isinstance(t, dict) else str(t)
                        for t in (profile.get("twitterHandles") or [])],
        }

    @staticmethod
    def _current_position(experience: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """The most likely *current* role: an ongoing one (no end date), else the
        one with the latest start date. LinkedIn doesn't return positions in a
        guaranteed order, so we derive it rather than trusting index 0."""
        if not experience:
            return None
        ongoing = [e for e in experience if not e.get("ends_at")]
        pool = ongoing or experience
        return max(pool, key=lambda e: (e.get("starts_at") or ""))

    # ── Assemble ─────────────────────────────────────────────────────────────

    def _structure_profile(
        self,
        profile_entity: Dict[str, Any],
        positions: List[Dict[str, Any]],
        educations: List[Dict[str, Any]],
        skills: List[Dict[str, Any]],
        certifications: List[Dict[str, Any]],
        languages: List[Dict[str, Any]],
        original_url: str,
        slug: str,
    ) -> Dict[str, Any]:
        """Normalise dash entities into a clean, flat-ish structured dict."""
        identity = self._parse_identity(profile_entity, slug)
        experience = self._parse_experience(positions)
        education = self._parse_education(educations)
        current = self._current_position(experience)

        # The profile-level location often resolves to only a country code; the
        # current role usually carries the richer "City, State, Country" string.
        if current and len(identity["location"]) <= 3 and current.get("location"):
            identity["location"] = current["location"]

        return {
            **identity,

            # ── LinkedIn identifiers ──
            "linkedin_url": f"https://www.linkedin.com/in/{slug}/",
            "original_url": original_url,

            # ── Current position (derived) ──
            "current_title": current["title"] if current else "",
            "current_company": current["company_name"] if current else "",

            # ── Contact (websites embedded in core; email/phone usually gated) ──
            "contact_info": self._parse_contact(profile_entity),

            # ── Sections ──
            "experience": experience,
            "education": education,
            "skills": self._parse_skills(skills),
            "certifications": self._parse_certifications(certifications),
            "languages": self._parse_languages(languages),
        }


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _dash_date(date_range: Optional[Dict[str, Any]], which: str) -> Optional[str]:
    """Dash dateRange {'start': {'year':2020,'month':6}, 'end': {...}} → '2020-06'.

    `which` is 'start' or 'end'. Returns None when the bound is absent
    (e.g. an ongoing role has no 'end').
    """
    if not isinstance(date_range, dict):
        return None
    part = date_range.get(which)
    return _format_date_dict(part)


def _format_date_dict(part: Optional[Dict[str, Any]]) -> Optional[str]:
    """A single {'year':2020,'month':6} date dict → '2020-06' (or '2020')."""
    if not isinstance(part, dict):
        return None
    year = part.get("year")
    month = part.get("month")
    if year and month:
        return f"{year}-{int(month):02d}"
    if year:
        return str(year)
    return None


def _humanize_enum(value: Any) -> str:
    """LinkedIn enum → human text: 'PROFESSIONAL_WORKING' → 'Professional Working'."""
    if not isinstance(value, str) or not value:
        return ""
    # Already human (contains a space or lowercase) → leave as-is.
    if " " in value or value != value.upper():
        return value
    return value.replace("_", " ").title()


# ──────────────────────────────────────────────────────────────────────────────
# Process-wide singleton
# ──────────────────────────────────────────────────────────────────────────────

_profile_service: "LinkedInProfileService | None" = None
_profile_service_lock = threading.Lock()


def get_linkedin_profile_service() -> LinkedInProfileService:
    """Return a shared, authenticated LinkedInProfileService (thread-safe)."""
    global _profile_service
    if _profile_service is None:
        with _profile_service_lock:
            if _profile_service is None:
                _profile_service = LinkedInProfileService()
    return _profile_service
