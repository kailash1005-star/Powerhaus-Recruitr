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

from app.services import match_qa_service as qa
from app.services.llm_extraction_service import ExtractionError

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


def _entry_for_marina() -> Dict[str, Any]:
    from app.services.matching_service import _score_candidate
    score, subscores, gaps, breakdown = _score_candidate(JD, MARINA, 0.45)
    return {
        "candidateId": "cid-marina", "fullName": "Marina W.",
        "score": score, "subscores": subscores, "gaps": gaps,
        "breakdown": breakdown, "partial": breakdown["partialMustHave"],
        "reasons": ["original reason"], "judge": {"verdict": "Stretch"},
        "reasoning": "judge",
    }


async def _run_audit(monkeypatch, llm_response) -> tuple[Dict[str, Any], Dict[str, Any], _FakeDb]:
    entry = _entry_for_marina()
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
        sims_by_cid={"cid-marina": 0.45},
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
        """One verified skill that doesn't cross a coverage-ceiling band leaves
        the (capped) score unchanged — and the auditor must NOT claim a
        correction it didn't make. The flag still lands in the report."""
        resp = {"candidates": [{
            "id": "cid-marina",
            "falseNegatives": [
                {"skill": "SAP HR",
                 "quote": "Schwerpunkten SAP HCM PA, PY, OM",
                 "why": "HCM is SAP's HR module family."},
            ],
            "falsePositives": [],
        }]}
        summary, entry, db = await _run_audit(monkeypatch, resp)
        assert summary["fnFlagsVerified"] == 1
        assert summary["fnCorrected"] == 0
        assert entry["score"] == _entry_for_marina()["score"]
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
