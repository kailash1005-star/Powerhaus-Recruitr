"""QA auditor guardrails (FC-30).

The auditor's incentive is inverted — it earns only by catching mistakes — so
these tests pin the referee mechanisms that stop that incentive being gamed,
and the asymmetry that stops the auditor itself creating harm:

  * a flag's quote must literally appear in the evidence (no quote → no effect);
  * verified flags rescore through the real scorer — never a hand-set number;
  * corrections only ever RAISE a score (a "correction" downward is discarded);
  * false-positive flags annotate and count — they never touch the score;
  * an auditor outage skips the audit and completes the run (fail-open);
  * the admin gate: allowlisted email or admin role, everyone else 403-style.

All offline: the LLM call is monkeypatched, Mongo is a stub.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.matching import match_qa_service as qa
from app.services.matching.llm_extraction_service import ExtractionError

from tests.test_match_evidence import JD, MARINA


# ── Quote verification (the mechanical referee) ──────────────────────────────

class TestVerifyQuote:
    CORPUS = ["○ Betreuung … Schwerpunkten SAP HCM PA, PY, OM, ESS/MSS, Success Factors"]

    def test_verbatim_quote_passes(self):
        assert qa.verify_quote("SAP HCM PA, PY, OM", self.CORPUS)

    def test_wrapping_and_case_are_forgiven(self):
        assert qa.verify_quote("sap hcm pa,\npy, om", self.CORPUS)

    def test_paraphrase_fails(self):
        assert not qa.verify_quote("works on SAP's HR modules", self.CORPUS)

    def test_fabricated_quote_fails(self):
        assert not qa.verify_quote("zehn Jahre SAP Customizing Erfahrung", self.CORPUS)

    def test_too_short_to_verify_fails(self):
        # "PY" alone appears — but a 2-char "quote" verifies nothing.
        assert not qa.verify_quote("PY", self.CORPUS)


# ── The audit pass ───────────────────────────────────────────────────────────

class _FakeCol:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    async def insert_one(self, doc):
        self.docs.append(doc)

        class _R:
            inserted_id = "qa-report-1"
        return _R()


class _FakeDb(dict):
    def __init__(self):
        super().__init__()
        self.col = _FakeCol()

    def __getitem__(self, name):
        return self.col


def _entry_for_marina(sim: float = 0.45) -> Dict[str, Any]:
    from app.services.matching.matching_service import _score_candidate
    score, subscores, gaps, breakdown = _score_candidate(JD, MARINA, sim)
    return {
        "candidateId": "cid-marina", "fullName": "Marina W.",
        "score": score, "subscores": subscores, "gaps": gaps,
        "breakdown": breakdown, "partial": breakdown["partialMustHave"],
        "reasons": ["original reason"], "judge": {"verdict": "Stretch"},
        "reasoning": "judge",
    }


async def _run_audit(monkeypatch, llm_response, sim: float = 0.45) -> tuple[Dict[str, Any], Dict[str, Any], _FakeDb]:
    entry = _entry_for_marina(sim)
    db = _FakeDb()
    if isinstance(llm_response, Exception):
        def fake_audit(requirements, batch):
            raise llm_response
    else:
        def fake_audit(requirements, batch):
            return llm_response
    monkeypatch.setattr(qa, "_audit_sync", fake_audit)
    summary = await qa.audit_run(
        db, match_run_id="run-1", pipeline_id="p", job_id="j",
        jd_title="SAP-HCM Specialist", requirements=JD,
        entries=[entry], profiles_by_cid={"cid-marina": MARINA},
        sims_by_cid={"cid-marina": sim},
    )
    return summary, entry, db


class TestAuditRun:
    async def test_verified_fn_corrects_upward_via_real_scorer(self, monkeypatch):
        resp = {"candidates": [{
            "id": "cid-marina",
            "falseNegatives": [
                {"skill": "SAP HR",
                 "quote": "Schwerpunkten SAP HCM PA, PY, OM",
                 "why": "HCM is SAP's HR module family."},
                {"skill": "SAP HR Processes",
                 "quote": "personalwirtschaftlichen Themen im SAP Umfeld",
                 "why": "German for HR processes run in SAP."},
            ],
            "falsePositives": [],
        }]}
        summary, entry, db = await _run_audit(monkeypatch, resp)
        assert summary["status"] == "completed"
        assert summary["fnFlagsVerified"] == 2 and summary["fnCorrected"] == 1
        assert entry["qa"]["corrected"] is True
        assert entry["score"] > entry["qa"]["originalScore"]
        assert "SAP HR" not in entry["gaps"]
        # The poisoned judge verdict must not survive into the corrected entry.
        assert entry["judge"] is None
        assert entry["reasoning"] == "qa_corrected"
        assert entry["qa"]["previousJudge"] == {"verdict": "Stretch"}
        # Report persisted with the correction.
        assert db.col.docs and db.col.docs[0]["scoreCorrections"][0]["skills"] == [
            "SAP HR", "SAP HR Processes"]

    async def test_correction_within_same_ceiling_band_is_a_noop(self, monkeypatch):
        """One verified skill that doesn't cross a coverage-ceiling band leaves a
        CAPPED score unchanged — and the auditor must NOT claim a correction it
        didn't make. The flag still lands in the report. sim=0.6 puts Marina's
        base above her 0.25-band ceiling (50), so crediting SAP HR (still in that
        band) can't move the capped score."""
        resp = {"candidates": [{
            "id": "cid-marina",
            "falseNegatives": [
                {"skill": "SAP HR",
                 "quote": "Schwerpunkten SAP HCM PA, PY, OM",
                 "why": "HCM is SAP's HR module family."},
            ],
            "falsePositives": [],
        }]}
        summary, entry, db = await _run_audit(monkeypatch, resp, sim=0.6)
        assert summary["fnFlagsVerified"] == 1
        assert summary["fnCorrected"] == 0
        assert entry["score"] == _entry_for_marina(0.6)["score"]
        assert db.col.docs[0]["perCandidate"][0]["correctedScore"] is None

    async def test_unverifiable_quote_is_discarded_with_no_effect(self, monkeypatch):
        resp = {"candidates": [{
            "id": "cid-marina",
            "falseNegatives": [
                {"skill": "SAP Troubleshooting",
                 "quote": "extensive troubleshooting of SAP systems",  # fabricated
                 "why": "sounds plausible"},
            ],
            "falsePositives": [],
        }]}
        summary, entry, _ = await _run_audit(monkeypatch, resp)
        assert summary["fnFlagsVerified"] == 0 and summary["fnCorrected"] == 0
        assert "qa" not in entry or not (entry.get("qa") or {}).get("corrected")
        assert "SAP Troubleshooting" in entry["gaps"]

    async def test_fp_flag_annotates_but_never_touches_the_score(self, monkeypatch):
        resp = {"candidates": [{
            "id": "cid-marina",
            "falseNegatives": [],
            "falsePositives": [
                {"skill": "SAP-HCM", "why": "title-only evidence, no project detail"},
            ],
        }]}
        before = _entry_for_marina()["score"]
        summary, entry, _ = await _run_audit(monkeypatch, resp)
        assert summary["fpFlagsRaised"] == 1
        assert entry["score"] == before
        assert entry["qa"]["falsePositives"][0]["skill"] == "SAP-HCM"
        assert entry["gaps"] == _entry_for_marina()["gaps"]

    async def test_flag_on_skill_outside_jd_is_ignored(self, monkeypatch):
        resp = {"candidates": [{
            "id": "cid-marina",
            "falseNegatives": [
                {"skill": "Recruiting",  # in her profile, NOT in the JD
                 "quote": "Recruiting für die genannten Berufe", "why": "…"},
            ],
            "falsePositives": [],
        }]}
        summary, entry, _ = await _run_audit(monkeypatch, resp)
        assert summary["fnFlagsRaised"] == 0 and summary["fnCorrected"] == 0

    async def test_auditor_outage_fails_open(self, monkeypatch):
        summary, entry, db = await _run_audit(
            monkeypatch, ExtractionError("model down"))
        assert summary["status"] == "skipped"
        assert entry["score"] == _entry_for_marina()["score"]
        # The skip itself is on the record — an un-audited run must be visible.
        assert db.col.docs and db.col.docs[0]["status"] == "skipped"

    async def test_clean_verdict_produces_empty_report(self, monkeypatch):
        resp = {"candidates": [{"id": "cid-marina",
                                "falseNegatives": [], "falsePositives": []}]}
        summary, entry, db = await _run_audit(monkeypatch, resp)
        assert summary["fnFlagsRaised"] == 0 and summary["fpFlagsRaised"] == 0
        assert db.col.docs[0]["perCandidate"] == []


# ── Rate-limit survival (2026-08-03) ─────────────────────────────────────────
# A 50-candidate run died on a 429 ("Limit 30000, Used 16863") and degraded to
# status="skipped" — 50 candidates silently un-audited. Two independent
# mechanisms keep that from recurring: pacing between batches (avoid the
# ceiling) and a wait that outlasts the provider's window (recover from it).

class _Boom(Exception):
    """Stand-in for the SDK's rate-limit error (status + headers, no import)."""
    def __init__(self, message, status_code=None, headers=None):
        super().__init__(message)
        self.status_code = status_code
        if headers is not None:
            self.response = type("_R", (), {"status_code": status_code, "headers": headers})()


class TestRateLimitWait:
    def test_429_waits_out_the_full_minute_window(self):
        from app.services.matching import llm_extraction_service as llm
        wait = llm._rate_limit_wait(_Boom("Error code: 429 - rate limit reached", 429))
        # Must outlast a per-MINUTE budget — the old ≤6s backoff never could.
        assert wait is not None and wait >= 60.0

    def test_a_normal_error_keeps_the_short_backoff(self):
        from app.services.matching import llm_extraction_service as llm
        assert llm._rate_limit_wait(_Boom("connection reset by peer")) is None

    def test_provider_retry_after_hint_is_honoured(self):
        from app.services.matching import llm_extraction_service as llm
        exc = _Boom("429 rate limit", 429, {"retry-after": "12"})
        assert llm._rate_limit_wait(exc) == 12.0

    def test_absurd_retry_after_is_capped(self):
        """A bogus header must not park a worker thread for an hour."""
        from app.services.matching import llm_extraction_service as llm
        exc = _Boom("429 rate limit", 429, {"retry-after": "99999"})
        assert llm._rate_limit_wait(exc) == llm._RATE_LIMIT_WAIT_CAP


class TestBatchPacing:
    """Chunking alone never fixed this — the ceiling is tokens per MINUTE, so
    the same profiles in more back-to-back calls trip the identical 429. Only
    the delay BETWEEN calls helps."""

    async def _audit_n(self, monkeypatch, n: int, pause: float):
        monkeypatch.setattr(qa.settings, "MATCH_QA_BATCH_PAUSE_SECS", pause)
        entries, profiles, sims = [], {}, {}
        for i in range(n):
            e = _entry_for_marina()
            e["candidateId"] = f"cid-{i}"
            entries.append(e)
            profiles[f"cid-{i}"] = MARINA
            sims[f"cid-{i}"] = 0.45
        monkeypatch.setattr(qa, "_audit_sync", lambda requirements, batch: {"candidates": []})

        slept: List[float] = []

        async def fake_sleep(secs):
            slept.append(secs)
        monkeypatch.setattr(qa.asyncio, "sleep", fake_sleep)

        summary = await qa.audit_run(
            _FakeDb(), match_run_id="run-1", pipeline_id="p", job_id="j",
            jd_title="SAP-HCM Specialist", requirements=JD,
            entries=entries, profiles_by_cid=profiles, sims_by_cid=sims,
        )
        return summary, slept

    async def test_multi_batch_run_pauses_between_batches(self, monkeypatch):
        # 25 candidates at batch size 12 → 3 batches → exactly 2 gaps.
        summary, slept = await self._audit_n(monkeypatch, 25, pause=15.0)
        assert summary["status"] == "completed"
        assert slept == [15.0, 15.0]

    async def test_single_batch_run_never_pauses(self, monkeypatch):
        """The common small run must not get slower for a problem it can't hit."""
        summary, slept = await self._audit_n(monkeypatch, 5, pause=15.0)
        assert summary["status"] == "completed"
        assert slept == []

    async def test_pause_of_zero_disables_pacing(self, monkeypatch):
        summary, slept = await self._audit_n(monkeypatch, 25, pause=0.0)
        assert summary["status"] == "completed"
        assert slept == []


# ── Admin gate ───────────────────────────────────────────────────────────────

class TestAdminGate:
    def _principal(self, email=None, roles=()):
        from app.security.deps import Principal
        return Principal(sub="auth0|x", email=email, roles=tuple(roles))

    def test_allowlisted_email_passes(self, monkeypatch):
        from app.api.v1 import qa as qa_api
        monkeypatch.setattr(qa_api.settings, "ADMIN_EMAILS",
                            "kailash@vanceltech.com, sudharsan@vanceltech.com")
        assert qa_api.is_admin(self._principal(email="Kailash@Vanceltech.com"))
        assert qa_api.is_admin(self._principal(email="sudharsan@vanceltech.com"))

    def test_admin_role_passes_without_email(self):
        from app.api.v1 import qa as qa_api
        assert qa_api.is_admin(self._principal(roles=("admin",)))

    def test_everyone_else_is_denied(self, monkeypatch):
        from app.api.v1 import qa as qa_api
        monkeypatch.setattr(qa_api.settings, "ADMIN_EMAILS", "kailash@vanceltech.com")
        assert not qa_api.is_admin(self._principal(email="client@beta-user.com"))
        assert not qa_api.is_admin(self._principal(email=None))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
