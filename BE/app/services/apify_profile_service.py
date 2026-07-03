"""
Apify LinkedIn Profile Enrichment Service.

Given LinkedIn profile URLs / public identifiers, fetch the FULL profile
(summary, experience-with-descriptions, education, skills, certifications,
languages, …) via the Apify actor ``harvestapi/linkedin-profile-scraper``.

────────────────────────────────────────────────────────────────────────────
Why the actor instead of our own LinkedIn dash service
────────────────────────────────────────────────────────────────────────────
The actor hits the SAME Voyager ``dash`` endpoints our
``linkedin_profile_service`` does (its output carries ``objectUrn``,
``publicIdentifier``, ``multiLocaleHeadline``, ``ACoAA…`` fsd_profile URNs) —
but runs its own pool of authenticated accounts + residential proxies as a
managed "no cookies" service, at ~$0.004/profile. So we hand the vendor the
entire cookie/proxy/throttle/ban-risk problem instead of maintaining it.

Apollo still supplies identity + company + the verified email; this service
supplies the résumé depth Apollo structurally lacks. The two are merged in
``candidate_merge``.

────────────────────────────────────────────────────────────────────────────
Cost & safety
────────────────────────────────────────────────────────────────────────────
  • One actor RUN handles the whole batch (10 profiles ≈ one ~16s run).
  • ``APIFY_ENRICH_MAX`` caps profiles-per-call as a runaway-cost guard; a
    breach raises BEFORE any spend.
  • Non-SUCCEEDED runs raise ``ApifyEnrichmentError`` (transient — retry once).
  • A requested profile that comes back with no item is reported per-identifier
    (private / not-found) rather than failing the batch.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from app.config import settings

logger = logging.getLogger(__name__)

# Apify pay-per-event price for the profile-only mode ($4 / 1000 profiles).
# Used only for a cost log line, not billing.
_COST_PER_PROFILE = 0.004


# ──────────────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────────────

class ApifyEnrichmentError(Exception):
    """Base error for the Apify enrichment service."""


class ApifyNotConfigured(ApifyEnrichmentError):
    """APIFY_TOKEN is missing — the actor cannot be called."""


class ApifyCostGuard(ApifyEnrichmentError):
    """The requested batch exceeds APIFY_ENRICH_MAX (refused before any spend)."""


class ApifyRunFailed(ApifyEnrichmentError):
    """The actor run finished in a non-SUCCEEDED state (transient — retry once)."""


# ──────────────────────────────────────────────────────────────────────────────
# Identifier normalization (the key we index actor results by)
# ──────────────────────────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)


def normalize_identifier(url_or_slug: str) -> Optional[str]:
    """Reduce a LinkedIn URL / bare slug to a lowercase public-identifier key.

      https://www.linkedin.com/in/Sudharsan2618/            → 'sudharsan2618'
      http://linkedin.com/in/foo?x=1                         → 'foo'
      'Sudharsan2618'                                        → 'sudharsan2618'
      .../in/andreas-steverm%c3%bcer-474  → 'andreas-stevermüer-474'  (percent-decoded)

    Non-ASCII slugs (umlauts, accents) arrive percent-encoded from Apollo; the
    actor resolves the decoded form, so we ``unquote`` before returning.
    """
    if not url_or_slug:
        return None
    s = url_or_slug.strip()
    if "/" not in s and "." not in s:
        return unquote(s).lower().rstrip("/") or None
    m = _SLUG_RE.search(s)
    if m:
        return unquote(m.group(1)).lower().rstrip("/") or None
    logger.warning("[Apify] could not normalize identifier: %s", url_or_slug)
    return None


def _result_keys(item: Dict[str, Any]) -> List[str]:
    """All identifier keys an actor item might be looked up by.

    The actor echoes the profile under ``publicIdentifier`` and ``linkedinUrl``;
    index by both (normalized) so callers can match on whatever they passed.
    """
    keys: List[str] = []
    pid = item.get("publicIdentifier")
    if pid:
        keys.append(str(pid).lower())
    for norm in (normalize_identifier(item.get("linkedinUrl") or ""),):
        if norm and norm not in keys:
            keys.append(norm)
    return keys


# ──────────────────────────────────────────────────────────────────────────────
# Service
# ──────────────────────────────────────────────────────────────────────────────

class ApifyProfileService:
    """Enrich LinkedIn profiles in bulk via the HarvestAPI Apify actor."""

    def __init__(self, token: Optional[str] = None, actor: Optional[str] = None) -> None:
        self._token = token or settings.APIFY_TOKEN
        self._actor = actor or settings.APIFY_PROFILE_ACTOR

    def _client(self):
        """Build the Apify client lazily (import here so the dep is only needed
        when enrichment actually runs)."""
        if not self._token:
            raise ApifyNotConfigured(
                "APIFY_TOKEN is not set — add it to BE/.env to enable enrichment."
            )
        try:
            from apify_client import ApifyClient
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ApifyEnrichmentError(
                "apify-client is not installed — `pip install apify-client`."
            ) from exc
        return ApifyClient(self._token)

    def enrich_profiles(self, identifiers: List[str]) -> Dict[str, Dict[str, Any]]:
        """Enrich a batch of LinkedIn URLs/slugs.

        Returns a dict keyed by the NORMALIZED identifier → raw actor profile
        item. Identifiers that produced no item are simply absent from the dict,
        so the caller can diff requested-vs-returned to detect not-found ones.

        Raises:
          ApifyCostGuard   — batch exceeds APIFY_ENRICH_MAX (no spend incurred).
          ApifyNotConfigured — APIFY_TOKEN missing.
          ApifyRunFailed   — the actor run did not SUCCEED.
        """
        # De-dup while preserving order; drop unparseable identifiers.
        wanted: List[str] = []
        seen: set[str] = set()
        for raw in identifiers:
            norm = normalize_identifier(raw)
            if norm and norm not in seen:
                seen.add(norm)
                wanted.append(norm)

        if not wanted:
            return {}

        if len(wanted) > settings.APIFY_ENRICH_MAX:
            raise ApifyCostGuard(
                f"Refusing to enrich {len(wanted)} profiles — exceeds "
                f"APIFY_ENRICH_MAX={settings.APIFY_ENRICH_MAX}. Split the batch."
            )

        run_input = {
            "profileScraperMode": settings.APIFY_PROFILE_MODE,
            # Pass full URLs; the actor accepts URLs or bare identifiers here.
            "queries": [f"https://www.linkedin.com/in/{ident}" for ident in wanted],
        }

        logger.info(
            "[Apify] enriching %d profile(s) via %s (est. $%.3f)",
            len(wanted), self._actor, len(wanted) * _COST_PER_PROFILE,
        )

        client = self._client()
        try:
            run = client.actor(self._actor).call(run_input=run_input)
        except Exception as exc:  # network / actor-call failure
            raise ApifyRunFailed(f"Apify actor call failed: {exc}") from exc

        status = (run or {}).get("status")
        if status != "SUCCEEDED":
            raise ApifyRunFailed(
                f"Apify run status {status!r} (expected SUCCEEDED). "
                "Transient — retry the batch."
            )

        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise ApifyRunFailed("Apify run returned no defaultDatasetId.")

        results: Dict[str, Dict[str, Any]] = {}
        for item in client.dataset(dataset_id).iterate_items():
            if not isinstance(item, dict):
                continue
            # Skip actor "error" items (e.g. a private/blocked profile row).
            if item.get("error") and not item.get("publicIdentifier"):
                continue
            for key in _result_keys(item):
                results.setdefault(key, item)

        found = {k for k in wanted if k in results}
        missing = [k for k in wanted if k not in found]
        if missing:
            logger.warning(
                "[Apify] %d/%d profiles returned no data (private/not-found): %s",
                len(missing), len(wanted), ", ".join(missing[:10]),
            )
        logger.info("[Apify] enriched %d/%d profiles", len(found), len(wanted))
        return results


# Process-wide default instance (cheap; holds only config).
_service: Optional[ApifyProfileService] = None


def get_apify_profile_service() -> ApifyProfileService:
    """Return a shared ApifyProfileService."""
    global _service
    if _service is None:
        _service = ApifyProfileService()
    return _service
