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

import inspect
import logging
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from app.config import settings

logger = logging.getLogger(__name__)


def call_actor_bounded(client: Any, actor_id: str, run_input: Dict[str, Any],
                       *, timeout_secs: int) -> Any:
    """Run an actor with a bounded server run-time AND client wait.

    Without a timeout, ``ActorClient.call()`` waits indefinitely and a hung
    actor leaves the job stuck on "running" forever. The kwarg NAMES differ by
    apify-client version (3.x: ``run_timeout``/``wait_duration`` as timedeltas;
    1.x/2.x: ``timeout_secs``/``wait_secs`` as ints), and our pin is loose
    (``>=1.7.0``), so we introspect the installed ``.call()`` and pass whichever
    it accepts. That way a version bump can't silently reintroduce the unbounded
    wait, and can't crash with "unexpected keyword argument" either.
    """
    actor = client.actor(actor_id)
    params = inspect.signature(actor.call).parameters
    kwargs: Dict[str, Any] = {"run_input": run_input}
    if "run_timeout" in params:            # apify-client 3.x
        kwargs["run_timeout"] = timedelta(seconds=timeout_secs)
        if "wait_duration" in params:
            kwargs["wait_duration"] = timedelta(seconds=timeout_secs + 60)
    elif "timeout_secs" in params:         # apify-client 1.x / 2.x
        kwargs["timeout_secs"] = timeout_secs
        if "wait_secs" in params:
            kwargs["wait_secs"] = timeout_secs + 60
    return actor.call(**kwargs)

# Apify pay-per-event price for the profile-only mode ($4 / 1000 profiles).
# Used only for a cost log line, not billing.
_COST_PER_PROFILE = 0.004


def _run_to_dict(run: Any) -> Dict[str, Any]:
    """Normalize an actor-run result to a plain dict.

    ``apify-client`` < 2 returns the run info as a ``dict``; 2.x returns a
    pydantic ``Run`` model (no ``.get``). Pinning is loose (``>=1.7.0``), so a
    container may resolve either — normalize both to a dict with camelCase keys
    matching the historical dict shape (``status``, ``defaultDatasetId``).
    """
    if not run:
        return {}
    if isinstance(run, dict):
        return run
    # pydantic v2 model → dict (by_alias gives the camelCase keys used below)
    model_dump = getattr(run, "model_dump", None)
    if callable(model_dump):
        try:
            data = model_dump(by_alias=True)
            if isinstance(data, dict):
                return data
        except Exception:  # pragma: no cover - defensive
            pass
    # Last resort: pull the two fields we need off attributes (snake or camel).
    return {
        "status": getattr(run, "status", None),
        "defaultDatasetId": (
            getattr(run, "defaultDatasetId", None)
            or getattr(run, "default_dataset_id", None)
        ),
    }


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


class ApifyQuotaExceeded(ApifyEnrichmentError):
    """The Apify account plan blocked the run/items (per-run item cap, total-run
    cap, or credit limit). NOT transient — needs a plan upgrade, so it is
    surfaced to the UI verbatim instead of being treated as 'profile not found'.
    """


# Substrings that mark a dataset "error" row as an account/plan block (as opposed
# to a per-profile private/not-found error). Matched case-insensitively.
_QUOTA_ERROR_MARKERS = (
    "limited to", "upgrade to a paid plan", "item limit", "run limit",
    "monthly usage", "usage limit", "not enough", "exceeded",
)


def _is_quota_error(msg: Any) -> bool:
    m = str(msg or "").lower()
    return any(marker in m for marker in _QUOTA_ERROR_MARKERS)


# ──────────────────────────────────────────────────────────────────────────────
# Identifier normalization (the key we index actor results by)
# ──────────────────────────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)
# LinkedIn member-URN identifiers (from the profile-search actor) look like
# ``ACoAA…`` / ``ACwAA…`` — a long base64url token. Unlike public vanity slugs
# (which are case-insensitive), these are CASE-SENSITIVE, so we must not
# lowercase them or the profile scraper won't resolve them.
_URN_RE = re.compile(r"^AC[A-Za-z0-9_-]{16,}$")


def normalize_identifier(url_or_slug: str) -> Optional[str]:
    """Reduce a LinkedIn URL / bare slug to a public-identifier key.

      https://www.linkedin.com/in/Sudharsan2618/            → 'sudharsan2618'
      http://linkedin.com/in/foo?x=1                         → 'foo'
      'Sudharsan2618'                                        → 'sudharsan2618'
      .../in/andreas-steverm%c3%bcer-474  → 'andreas-stevermüer-474'  (percent-decoded)
      .../in/ACwAADXD9RAB…               → 'ACwAADXD9RAB…'  (URN — case preserved)

    Vanity slugs are lowercased (case-insensitive); member URNs keep their case.
    """
    if not url_or_slug:
        return None
    s = url_or_slug.strip()
    raw: Optional[str]
    if "/" not in s and "." not in s:
        raw = unquote(s).rstrip("/") or None
    else:
        m = _SLUG_RE.search(s)
        if not m:
            logger.warning("[Apify] could not normalize identifier: %s", url_or_slug)
            return None
        raw = unquote(m.group(1)).rstrip("/") or None
    if not raw:
        return None
    return raw if _URN_RE.match(raw) else raw.lower()


def _result_keys(item: Dict[str, Any]) -> List[str]:
    """All identifier keys an actor item might be looked up by.

    Callers may key by a vanity slug (Apollo) OR a member URN (profile-search
    actor). The scraper echoes ``publicIdentifier`` + ``linkedinUrl`` and often
    the original ``id``/``profileId``; index by all of them so either kind of
    input matches.

    The HarvestAPI actor also echoes the original input URL in ``query`` and
    stores the member URN in ``objectUrn`` / ``profileUrn`` / ``memberUrn``.
    We must index by those too, otherwise URN-based lookups from the search
    actor silently fail (the actor scrapes the profile but the result can't be
    matched back to the requested URN identifier).
    """
    keys: List[str] = []

    def _add(v: Any) -> None:
        if v and str(v) not in keys:
            keys.append(str(v))

    pid = item.get("publicIdentifier")
    if pid:
        _add(str(pid).lower())
    # Raw URN-style ids (case preserved) the caller may have keyed on.
    for raw in (item.get("id"), item.get("profileId"), item.get("profileIdInSearch")):
        if raw and _URN_RE.match(str(raw)):
            _add(str(raw))
    _add(normalize_identifier(item.get("linkedinUrl") or ""))

    # The HarvestAPI actor echoes the original query URL (e.g.
    # "https://www.linkedin.com/in/ACwAAGAJg5YB…") — extract its identifier
    # so URN-based lookups match the result back to the requested candidate.
    _add(normalize_identifier(item.get("query") or ""))

    # URN fields returned by the actor (e.g. "urn:li:fsd_profile:ACwAA…" or
    # "urn:li:member:12345"). Extract the AC… token if present.
    for urn_field in ("objectUrn", "profileUrn", "memberUrn"):
        urn_val = item.get(urn_field)
        if urn_val:
            # Strip the "urn:li:…:" prefix to get the bare identifier.
            bare = str(urn_val).rsplit(":", 1)[-1]
            if _URN_RE.match(bare):
                _add(bare)

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

        client = self._client()

        # Chunk into runs of APIFY_ENRICH_BATCH: the actor refuses a run whose
        # item count exceeds the plan's per-run cap and returns ZERO profiles, so
        # a single big run would yield nothing. Smaller runs let partial data
        # through. A plan/quota block raises ApifyQuotaExceeded so the caller can
        # tell the user "upgrade" instead of silently marking every profile
        # not-found — but only if NO profile came back at all; partial success is
        # returned as-is.
        batch = max(1, int(settings.APIFY_ENRICH_BATCH or 10))
        results: Dict[str, Dict[str, Any]] = {}
        quota_msg: Optional[str] = None
        for start in range(0, len(wanted), batch):
            chunk = wanted[start:start + batch]
            try:
                self._run_chunk(client, chunk, results)
            except ApifyQuotaExceeded as exc:
                quota_msg = str(exc)
                logger.warning("[Apify] plan/quota block at chunk %d: %s", start // batch, exc)
                break  # further runs would hit the same cap

        found = {k for k in wanted if k in results}
        if not found and quota_msg:
            # Nothing at all came back and the reason is a plan block → surface it.
            raise ApifyQuotaExceeded(quota_msg)

        missing = [k for k in wanted if k not in found]
        if missing:
            logger.warning(
                "[Apify] %d/%d profiles returned no data (private/not-found%s): %s",
                len(missing), len(wanted),
                "; plan cap hit" if quota_msg else "", ", ".join(missing[:10]),
            )
        logger.info("[Apify] enriched %d/%d profiles", len(found), len(wanted))
        return results

    def _run_chunk(
        self, client: Any, chunk: List[str], results: Dict[str, Dict[str, Any]]
    ) -> None:
        """Run the scraper for one chunk of ≤batch identifiers, folding the
        returned profiles into ``results`` (keyed by every id they can match).
        Raises ApifyQuotaExceeded if the run comes back as an account/plan block.
        """
        run_input = {
            "profileScraperMode": settings.APIFY_PROFILE_MODE,
            # Pass full URLs; the actor accepts URLs or bare identifiers here.
            "queries": [f"https://www.linkedin.com/in/{ident}" for ident in chunk],
        }
        logger.info(
            "[Apify] enriching %d profile(s) via %s (est. $%.3f)",
            len(chunk), self._actor, len(chunk) * _COST_PER_PROFILE,
        )
        try:
            run = call_actor_bounded(
                client, self._actor, run_input,
                timeout_secs=settings.APIFY_CALL_TIMEOUT_SECS,
            )
        except Exception as exc:  # network / actor-call failure
            raise ApifyRunFailed(f"Apify actor call failed: {exc}") from exc

        run_info = _run_to_dict(run)
        status = run_info.get("status")
        if status != "SUCCEEDED":
            raise ApifyRunFailed(
                f"Apify run status {status!r} (expected SUCCEEDED). "
                "Transient — retry the batch."
            )

        dataset_id = run_info.get("defaultDatasetId") or run_info.get("default_dataset_id")
        if not dataset_id:
            raise ApifyRunFailed("Apify run returned no defaultDatasetId.")

        n_found_before = len(results)
        # The HarvestAPI actor processes queries SEQUENTIALLY — item#1
        # corresponds to chunk[0], item#2 to chunk[1], etc. We MUST use this
        # positional correlation because:
        #   • The search actor returns MEMBER URNs (ACw…)
        #   • The profile scraper returns PROFILE URNs (ACo…) in ``id``
        #   • These are DIFFERENT identifiers for the same person
        #   • The actor does NOT echo back the original query URL
        # So field-based matching (publicIdentifier, linkedinUrl, id) alone
        # will never bridge the member-URN → profile-URN gap.
        chunk_idx = 0
        for item in client.dataset(dataset_id).iterate_items():
            if not isinstance(item, dict):
                continue
            # A bare error row with no profile identity is either an account/plan
            # block (fatal → raise) or a per-profile private/not-found row (skip).
            if item.get("error") and not item.get("publicIdentifier"):
                if _is_quota_error(item.get("error")):
                    raise ApifyQuotaExceeded(f"Apify plan limit: {item.get('error')}")
                chunk_idx += 1  # error row still occupies a query position
                continue
            # Index by every field-based key the item exposes (vanity slug,
            # profile URN, linkedinUrl, etc.) — works for Apollo/slug lookups.
            for key in _result_keys(item):
                results.setdefault(key, item)
            # Positional correlation: map the ORIGINAL input identifier
            # (the member URN the search actor gave us) to this result.
            if chunk_idx < len(chunk):
                results.setdefault(chunk[chunk_idx], item)
            chunk_idx += 1

        # Meter only the profiles this chunk actually added. Best-effort (metering
        # must not fail an enrichment that already succeeded) — but logged.
        added = len(results) - n_found_before
        try:
            from app.services import cost_service
            if added > 0:
                vendor_usd = run_info.get("usageTotalUsd") or run_info.get("usage_total_usd")
                cost_service.record_event(
                    service="apify", operation="profile_scrape",
                    unit="profile", quantity=added,
                    cost_override=(float(vendor_usd) if vendor_usd else None),
                    vendor_ref=str(run_info.get("id") or dataset_id),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Apify] cost metering failed (enrichment succeeded): %s", exc)


# Process-wide default instance (cheap; holds only config).
_service: Optional[ApifyProfileService] = None


def get_apify_profile_service() -> ApifyProfileService:
    """Return a shared ApifyProfileService."""
    global _service
    if _service is None:
        _service = ApifyProfileService()
    return _service
