"""Operator alerting: classification, dedup, and the never-break-the-caller contract.

The three things worth testing here map to the three ways this feature can fail
the operator:

  1. It names the WRONG fix — telling someone to rotate a healthy token while the
     account sits at its plan cap. Classification is the product; most of these
     tests are about it.
  2. It floods — one dead token fails every job, so without a cooldown the
     channel gets muted and the next real alert is missed.
  3. It breaks the pipeline it watches — an alerter that raises into a background
     job is worse than no alerter.

No network and no SMTP: delivery is stubbed, and the dedup store is a fake that
implements only the two Mongo operations the claim uses.
"""
from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta

import pytest

ah = importlib.import_module("app.services.sourcing.apify_health")
alerts = importlib.import_module("app.services.operations.alerts")


# ── Classification ───────────────────────────────────────────────────────────

class TestClassify:
    """The distinction that matters: which failures need which human action."""

    @pytest.mark.parametrize("code", [401, 403])
    def test_rejected_token_is_auth_and_says_rotate(self, code):
        v = ah.classify(Exception("nope"), status_code=code)
        assert v.kind == ah.KIND_AUTH
        assert v.is_actionable
        assert "rotate" in v.action.lower()

    def test_dead_token_was_previously_invisible(self):
        """Regression guard for the gap this feature exists to close.

        A 401 used to be wrapped as a retryable ApifyRunFailed, so the Broadener
        widened filters against a search that never ran and the recruiter was
        told "no candidates matched".
        """
        assert ah.classify(Exception("Unauthorized"), status_code=401).kind == ah.KIND_AUTH
        assert not ah.is_spend_error("Unauthorized")

    @pytest.mark.parametrize("msg", [
        "free user run limit reached",          # observed live, see SOURCING_FRICTION_ANALYSIS
        "You are limited to 10 items. Upgrade to a paid plan.",
        "Monthly usage exceeded",
        "insufficient credit",
    ])
    def test_plan_blocks_are_spend_and_warn_against_rotating(self, msg):
        v = ah.classify(msg)
        assert v.kind == ah.KIND_SPEND
        assert v.is_actionable
        # The costly mistake this guards: sending someone to rotate a key that is
        # working, on an account whose limit a new key would not change.
        assert "not help" in v.action.lower()

    def test_status_code_beats_message_text(self):
        """A 401 whose body happens to mention a quota is still a dead token."""
        v = ah.classify(Exception("quota information unavailable"), status_code=401)
        assert v.kind == ah.KIND_AUTH

    def test_rate_limit_is_not_actionable(self):
        """429 self-resolves. Emailing it is how an alert channel gets muted."""
        v = ah.classify(Exception("slow down"), status_code=429)
        assert v.kind == ah.KIND_RATE_LIMIT
        assert not v.is_actionable

    def test_unknown_failure_is_transient_not_actionable(self):
        v = ah.classify("ECONNRESET socket hang up")
        assert v.kind == ah.KIND_TRANSIENT
        assert not v.is_actionable

    def test_no_run_count_kind_exists(self):
        """The probe proved this plan has no monthly run ceiling — only a $ cap.

        A run-cap kind would need a threshold number nobody can measure, so
        "free user run limit reached" is classified as spend instead.
        See docs/engineering/APIFY_LIMITS.md.
        """
        assert not hasattr(ah, "KIND_RUN_CAP")
        assert ah.classify("free user run limit reached").kind == ah.KIND_SPEND


class TestTranslateVendorError:
    """The call-site wrapper that replaced two blanket `except Exception`s."""

    def setup_method(self):
        self.svc = importlib.import_module("app.services.sourcing.apify_profile_service")

    def _err(self, msg, code):
        e = Exception(msg)
        e.status_code = code
        return e

    def test_401_becomes_auth_failed(self):
        exc = self.svc.translate_vendor_error(self._err("bad token", 401), context="ctx")
        assert isinstance(exc, self.svc.ApifyAuthFailed)

    def test_plan_block_becomes_quota_exceeded(self):
        exc = self.svc.translate_vendor_error(Exception("upgrade your plan"), context="ctx")
        assert isinstance(exc, self.svc.ApifyQuotaExceeded)

    def test_unknown_stays_run_failed_so_broadener_still_aborts(self):
        """The Broadener catches ApifyRunFailed; unknown failures must keep that
        behaviour or the retry-abort path silently stops working."""
        exc = self.svc.translate_vendor_error(self._err("boom", 500), context="ctx")
        assert isinstance(exc, self.svc.ApifyRunFailed)

    def test_every_translation_stays_in_the_apify_hierarchy(self):
        for err in [self._err("x", 401), Exception("quota"), self._err("y", 500)]:
            assert isinstance(
                self.svc.translate_vendor_error(err, context="c"),
                self.svc.ApifyEnrichmentError,
            )


# ── Dedup ────────────────────────────────────────────────────────────────────

class FakeAlertStore:
    """Implements only what _claim_send uses: a conditional find_one_and_update
    and an upsert-with-$inc. Enough to prove the cooldown, without Mongo."""

    def __init__(self):
        self.docs = {}

    async def find_one_and_update(self, flt, update):
        key = flt["_id"]
        doc = self.docs.get(key)
        if doc is None:
            return None
        cutoff = flt.get("lastSentAt", {}).get("$lte")
        if cutoff is not None and doc["lastSentAt"] > cutoff:
            return None  # still cooling down
        before = dict(doc)
        doc.update(update["$set"])
        return before

    async def update_one(self, flt, update, upsert=False):
        key = flt["_id"]
        existed = key in self.docs
        if not existed:
            if not upsert:
                return type("R", (), {"upserted_id": None})()
            self.docs[key] = {**update.get("$setOnInsert", {}), "suppressedCount": 0}
        for k, v in (update.get("$inc") or {}).items():
            self.docs[key][k] = self.docs[key].get(k, 0) + v
        for k, v in (update.get("$set") or {}).items():
            self.docs[key][k] = v
        return type("R", (), {"upserted_id": None if existed else key})()


@pytest.fixture
def wired(monkeypatch):
    """Alerts fully armed, delivery captured instead of sent."""
    store = FakeAlertStore()
    sent = []

    async def fake_get_collection(name):
        return store

    async def fake_send(to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return {"sent": True}

    db = importlib.import_module("app.database")
    email = importlib.import_module("app.services.operations.email_service")
    monkeypatch.setattr(db, "get_collection", fake_get_collection)
    monkeypatch.setattr(email, "send_email", fake_send)
    monkeypatch.setattr(email, "email_configured", lambda: True)
    monkeypatch.setattr(alerts.settings, "ALERTS_ENABLED", True)
    monkeypatch.setattr(alerts.settings, "ALERT_RECIPIENTS", "ops@example.com")
    monkeypatch.setattr(alerts.settings, "ALERT_COOLDOWN_MINUTES", 60)
    return store, sent


class TestDedup:
    def test_first_alert_sends(self, wired):
        _, sent = wired
        assert asyncio.run(alerts.notify_operator(
            "t", "d", dedup_key="apify:auth", action="a")) is True
        assert len(sent) == 1

    def test_repeat_inside_cooldown_is_counted_not_sent(self, wired):
        """The property that makes email survivable: a dead token fails on every
        job of a run, and fifty identical emails would mute the channel."""
        store, sent = wired
        for _ in range(50):
            asyncio.run(alerts.notify_operator(
                "t", "d", dedup_key="apify:auth", action="a"))
        assert len(sent) == 1
        assert store.docs["apify:auth"]["suppressedCount"] == 49

    def test_after_cooldown_sends_again_and_reports_what_was_hidden(self, wired):
        store, sent = wired
        asyncio.run(alerts.notify_operator("t", "d", dedup_key="apify:auth", action="a"))
        for _ in range(4):
            asyncio.run(alerts.notify_operator("t", "d", dedup_key="apify:auth", action="a"))
        # Age the record past the cooldown.
        store.docs["apify:auth"]["lastSentAt"] = datetime.utcnow() - timedelta(hours=2)
        asyncio.run(alerts.notify_operator("t", "d", dedup_key="apify:auth", action="a"))
        assert len(sent) == 2
        # Throttling must never hide scale.
        assert "5 times" in sent[1]["body"]

    def test_distinct_causes_do_not_share_a_cooldown(self, wired):
        _, sent = wired
        asyncio.run(alerts.notify_operator("a", "d", dedup_key="apify:auth", action="x"))
        asyncio.run(alerts.notify_operator("b", "d", dedup_key="apify:spend", action="y"))
        assert len(sent) == 2

    def test_body_carries_the_remedy(self, wired):
        _, sent = wired
        asyncio.run(alerts.notify_operator(
            "Apify token rejected", "Token refused.",
            dedup_key="apify:auth", action="Rotate APIFY_TOKEN in BE/.env."))
        assert "WHAT TO DO" in sent[0]["body"]
        assert "Rotate APIFY_TOKEN" in sent[0]["body"]


class TestNeverBreaksTheCaller:
    """Monitoring must not be able to break the thing it monitors."""

    def test_silent_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(alerts.settings, "ALERTS_ENABLED", False)
        assert asyncio.run(alerts.notify_operator(
            "t", "d", dedup_key="k", action="a")) is False

    def test_smtp_raising_does_not_propagate(self, wired, monkeypatch):
        """send_email raises RuntimeError when unconfigured, and SMTP can throw
        at any time; neither may reach the pipeline."""
        email = importlib.import_module("app.services.operations.email_service")

        async def boom(*a, **k):
            raise RuntimeError("smtp exploded")

        monkeypatch.setattr(email, "send_email", boom)
        assert asyncio.run(alerts.notify_operator(
            "t", "d", dedup_key="k", action="a")) is True  # claimed, delivery failed

    def test_dedup_store_failure_does_not_propagate(self, monkeypatch):
        db = importlib.import_module("app.database")

        async def boom(name):
            raise RuntimeError("mongo down")

        monkeypatch.setattr(db, "get_collection", boom)
        monkeypatch.setattr(alerts.settings, "ALERTS_ENABLED", True)
        monkeypatch.setattr(alerts.settings, "ALERT_RECIPIENTS", "ops@example.com")
        email = importlib.import_module("app.services.operations.email_service")
        monkeypatch.setattr(email, "email_configured", lambda: True)
        assert asyncio.run(alerts.notify_operator(
            "t", "d", dedup_key="k", action="a")) is False

    def test_alert_if_actionable_swallows_everything(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("classifier exploded")

        monkeypatch.setattr(ah, "classify", boom)
        assert asyncio.run(ah.alert_if_actionable(Exception("x"), where="test")) is False


class TestAlertGate:
    """Only failures a human must act on reach the inbox."""

    def test_transient_does_not_alert(self, wired):
        _, sent = wired
        asyncio.run(ah.alert_if_actionable(Exception("ECONNRESET"), where="test"))
        assert sent == []

    def test_rate_limit_does_not_alert(self, wired):
        _, sent = wired
        e = Exception("slow"); e.status_code = 429
        asyncio.run(ah.alert_if_actionable(e, where="test"))
        assert sent == []

    def test_auth_alerts(self, wired):
        _, sent = wired
        e = Exception("bad token"); e.status_code = 401
        asyncio.run(ah.alert_if_actionable(e, where="discovery"))
        assert len(sent) == 1
        assert "discovery" in sent[0]["body"]

    def test_one_dead_token_across_many_jobs_sends_one_email(self, wired):
        """Dedup keys on the CAUSE, not the call site — the operator needs to be
        told once about the token, not once per job."""
        _, sent = wired
        for job in range(20):
            e = Exception("bad token"); e.status_code = 401
            asyncio.run(ah.alert_if_actionable(e, where=f"job {job}"))
        assert len(sent) == 1


# ── Account health ───────────────────────────────────────────────────────────

class TestAccountHealth:
    def test_pct_used_computed_from_measured_fields(self):
        h = ah.AccountHealth(ok=True, token_valid=True, usage_usd=4.0, max_usd=5.0)
        assert h.pct_used == 80.0

    def test_pct_none_when_unreadable_rather_than_reassuring(self):
        """A preflight that misreports 'you're fine' is worse than one that only
        checks the token, so an unreadable ceiling must not become 0%."""
        assert ah.AccountHealth(ok=True, usage_usd=4.0, max_usd=None).pct_used is None
        assert ah.AccountHealth(ok=False, detail="unreachable").pct_used is None

    def test_unreadable_account_does_not_alert(self, wired):
        """Apify being briefly unreachable is not an operator problem."""
        _, sent = wired

        async def unreadable():
            return ah.AccountHealth(ok=False, detail="network")

        import unittest.mock as m
        with m.patch.object(ah, "read_account", unreadable):
            asyncio.run(ah.preflight(source="test"))
        assert sent == []

    def test_rejected_token_alerts_on_preflight(self, wired):
        _, sent = wired

        async def rejected():
            return ah.AccountHealth(ok=True, token_valid=False, detail="HTTP 401")

        import unittest.mock as m
        with m.patch.object(ah, "read_account", rejected):
            asyncio.run(ah.preflight(source="test"))
        assert len(sent) == 1
        assert "rotate" in sent[0]["body"].lower()

    def test_spend_over_threshold_alerts(self, wired, monkeypatch):
        _, sent = wired
        monkeypatch.setattr(ah.settings, "APIFY_USAGE_WARN_PCT", 80)

        async def hot():
            return ah.AccountHealth(ok=True, token_valid=True, usage_usd=4.5,
                                    max_usd=5.0, cycle_ends="2026-08-21")

        import unittest.mock as m
        with m.patch.object(ah, "read_account", hot):
            asyncio.run(ah.preflight(source="test"))
        assert len(sent) == 1
        assert "not help" in sent[0]["body"].lower()

    def test_spend_below_threshold_is_silent(self, wired, monkeypatch):
        _, sent = wired
        monkeypatch.setattr(ah.settings, "APIFY_USAGE_WARN_PCT", 80)

        async def cool():
            return ah.AccountHealth(ok=True, token_valid=True, usage_usd=0.23, max_usd=5.0)

        import unittest.mock as m
        with m.patch.object(ah, "read_account", cool):
            asyncio.run(ah.preflight(source="test"))
        assert sent == []

    def test_threshold_zero_disables_the_spend_warning(self, wired, monkeypatch):
        _, sent = wired
        monkeypatch.setattr(ah.settings, "APIFY_USAGE_WARN_PCT", 0)

        async def maxed():
            return ah.AccountHealth(ok=True, token_valid=True, usage_usd=5.0, max_usd=5.0)

        import unittest.mock as m
        with m.patch.object(ah, "read_account", maxed):
            asyncio.run(ah.preflight(source="test"))
        assert sent == []
