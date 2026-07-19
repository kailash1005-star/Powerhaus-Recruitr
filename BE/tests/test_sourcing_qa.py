"""Sourcing-results audit guardrails (FC-32, FC-33).

Two mechanisms, deliberately split by whether the question is exact or fuzzy:

  * Location (FC-32) is EXACT → a deterministic gate. These tests pin that a
    wrong-country candidate is rejected, a wrong-region/right-country one is
    kept-and-flagged (remote/relocation is legitimate — a reject there would be
    the false-negative crime), and absent location never causes a reject.

  * Specialty (FC-33) is FUZZY → the LLM auditor. These tests pin that it flags
    (never deletes), respects a confidence floor, ignores location, and
    fails open.

Offline: the auditor LLM call is monkeypatched, Mongo is a stub.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services import location_resolver as lr
from app.services import sourcing_qa_service as sqa
from app.services.llm_extraction_service import ExtractionError


# ── Deterministic location gate ──────────────────────────────────────────────

class TestLocationVerdict:
    def test_wrong_country_is_rejected(self):
        v = lr.location_verdict("Bavaria, Germany", "Bengaluru, Karnataka, India")
        assert v["decision"] == "country_mismatch"
        assert v["requestedCountry"] == "germany" and v["candidateCountry"] == "india"

    def test_same_country_same_region_matches(self):
        v = lr.location_verdict("Bavaria, Germany", "Munich, Bavaria, Germany")
        assert v["decision"] == "match"

    def test_same_country_other_region_is_soft_flag(self):
        v = lr.location_verdict("Bavaria, Germany", "Hamburg, Germany")
        assert v["decision"] == "region_mismatch"  # kept, not rejected

    def test_country_aliases_resolve(self):
        assert lr.location_verdict("Germany", "München, Deutschland")["decision"] == "match"
        assert lr.location_verdict("USA", "New York, United States")["decision"] == "match"

    def test_missing_location_never_rejects(self):
        assert lr.location_verdict("Bavaria, Germany", "")["decision"] == "unknown"
        assert lr.location_verdict("", "Munich, Germany")["decision"] == "unknown"

    def test_requested_location_prefers_filter_over_jd(self):
        loc = lr.requested_location(
            {"locations": ["Bavaria, Germany"]}, {"location": "Berlin, Germany"})
        assert loc == "Bavaria, Germany"

    def test_requested_location_falls_back_to_jd(self):
        assert lr.requested_location({}, {"location": "Berlin, Germany"}) == "Berlin, Germany"

    def test_requested_location_none_when_absent(self):
        assert lr.requested_location({}, {}) is None


# ── Location gate wired into _store_profiles ─────────────────────────────────

class _FakeUpdateResult:
    upserted_id = "new-id"


class _FakeCandidatesCol:
    def __init__(self):
        self.upserts: List[Dict[str, Any]] = []

    async def update_one(self, flt, update, upsert=False):
        doc = update.get("$setOnInsert") or {}
        self.upserts.append(doc)
        return _FakeUpdateResult()

    async def find_one(self, *a, **k):
        return None


class TestStoreProfilesLocationGate:
    async def test_india_candidate_for_germany_search_is_rejected(self, monkeypatch):
        from app.services import candidate_pipeline as cp

        col = _FakeCandidatesCol()

        async def fake_get_collection(name):
            return col
        monkeypatch.setattr(cp, "get_collection", fake_get_collection)
        from app.config import settings as _settings
        monkeypatch.setattr(_settings, "SOURCING_LOCATION_GATE", "country")
        monkeypatch.setattr(_settings, "PRESCREEN_ENABLED", False)

        import datetime as _dt
        profiles = [
            {"profileId": "de", "currentTitle": "SAP HCM Consultant",
             "location": "Munich, Bavaria, Germany", "channels": ["title"]},
            {"profileId": "in", "currentTitle": "SAP HCM Consultant",
             "location": "Bengaluru, Karnataka, India", "channels": ["keyword"]},
        ]
        cand_ids, verdicts = await cp._store_profiles(
            profiles, pipeline_id="p", job_id="j", search_query="SAP HCM",
            now=_dt.datetime.utcnow(),
            requested_location="Bavaria, Germany",
        )
        # The German candidate is kept; the Indian one is stored-but-rejected.
        assert len(cand_ids) == 1
        india = next(v for v in verdicts if v["title"] == "SAP HCM Consultant"
                     and (v.get("location") or {}).get("candidateCountry") == "india")
        assert india["decision"] == "drop"
        rejected = next(d for d in col.upserts if d.get("locationMismatch"))
        assert "India" in rejected["rejectionReason"]

    async def test_gate_off_keeps_everyone(self, monkeypatch):
        from app.services import candidate_pipeline as cp
        col = _FakeCandidatesCol()

        async def fake_get_collection(name):
            return col
        monkeypatch.setattr(cp, "get_collection", fake_get_collection)
        from app.config import settings as _settings
        monkeypatch.setattr(_settings, "SOURCING_LOCATION_GATE", "off")
        monkeypatch.setattr(_settings, "PRESCREEN_ENABLED", False)

        import datetime as _dt
        profiles = [{"profileId": "in", "currentTitle": "SAP HCM Consultant",
                     "location": "Bengaluru, India", "channels": ["keyword"]}]
        cand_ids, _ = await cp._store_profiles(
            profiles, pipeline_id="p", job_id="j", search_query="q",
            now=_dt.datetime.utcnow(), requested_location="Germany")
        assert len(cand_ids) == 1


# ── Fuzzy specialty auditor ──────────────────────────────────────────────────

class _FakeCol:
    def __init__(self):
        self.reports: List[Dict[str, Any]] = []
        self.updates: List[tuple] = []

    async def insert_one(self, doc):
        self.reports.append(doc)

        class _R:
            inserted_id = "srep-1"
        return _R()

    async def update_one(self, flt, update, **k):
        self.updates.append((flt, update))


class _FakeDb:
    def __init__(self):
        self.qa = _FakeCol()
        self.candidates = _FakeCol()

    def __getitem__(self, name):
        return self.qa if name == "qa_reports" else self.candidates


# Valid 24-char ObjectId strings — the service casts candidateId to ObjectId for
# the annotation writeback, exactly as it will in production.
CID_A = "6a5c000000000000000000a1"
CID_B = "6a5c000000000000000000b2"
CID_C = "6a5c000000000000000000c3"
KEPT = [
    {"candidateId": CID_A, "title": "SAP HCM Consultant", "company": "X", "channels": ["title"]},
    {"candidateId": CID_B, "title": "SAP FICO Consultant", "company": "Y", "channels": ["keyword"]},
    {"candidateId": CID_C, "title": "SAP SuccessFactors Berater", "company": "Z", "channels": ["title", "keyword"]},
]
QUERY = {"title": "SAP HCM Consultant", "targetTitles": ["SAP HCM Consultant"],
         "mustHaveSkills": ["SAP HCM"], "seniority": None}


async def _run(monkeypatch, resp):
    db = _FakeDb()
    if isinstance(resp, Exception):
        def fake(query, cands):
            raise resp
    else:
        def fake(query, cands):
            return resp
    monkeypatch.setattr(sqa, "_audit_sync", fake)
    summary = await sqa.audit_results(
        db, pipeline_id="p", job_id="j", jd_title="SAP HCM Consultant",
        query=QUERY, kept=KEPT, location_rejected=2)
    return summary, db


class TestSourcingAuditor:
    async def test_flags_off_specialty_above_confidence_floor(self, monkeypatch):
        resp = {"mismatches": [
            {"id": CID_B, "reason": "SAP FICO is finance, not HR.",
             "likelyActualSpecialty": "SAP FICO", "confidence": 0.92},
        ]}
        summary, db = await _run(monkeypatch, resp)
        assert summary["mismatchesFlagged"] == 1
        assert summary["locationRejected"] == 2  # carried from the deterministic gate
        # Candidate row annotated, NEVER rejected.
        flt, update = db.candidates.updates[0]
        assert "$set" in update and "sourcingQaFlag" in update["$set"]
        assert "isAccepted" not in update["$set"]
        assert db.qa.reports[0]["kind"] == "sourcing"

    async def test_low_confidence_is_noted_not_flagged(self, monkeypatch):
        resp = {"mismatches": [
            {"id": CID_A, "reason": "maybe a generalist?",
             "likelyActualSpecialty": "HR generalist", "confidence": 0.4},
        ]}
        summary, db = await _run(monkeypatch, resp)
        assert summary["mismatchesFlagged"] == 0
        assert db.qa.reports[0]["metrics"]["lowConfidenceNoted"] == 1
        assert db.candidates.updates == []  # nothing annotated

    async def test_clean_result_flags_nothing(self, monkeypatch):
        summary, db = await _run(monkeypatch, {"mismatches": []})
        assert summary["mismatchesFlagged"] == 0
        assert db.qa.reports[0]["status"] == "completed"

    async def test_auditor_outage_fails_open(self, monkeypatch):
        summary, db = await _run(monkeypatch, ExtractionError("model down"))
        assert summary["status"] == "skipped"
        assert db.candidates.updates == []
        assert db.qa.reports[0]["status"] == "skipped"

    async def test_model_defaults_to_shared_superior_model(self, monkeypatch):
        monkeypatch.setattr(sqa.settings, "SOURCING_QA_MODEL", "")
        monkeypatch.setattr(sqa.settings, "QA_AUDITOR_MODEL", "gpt-4o")
        assert sqa.qa_model() == "gpt-4o"

    async def test_per_auditor_override_wins(self, monkeypatch):
        monkeypatch.setattr(sqa.settings, "SOURCING_QA_MODEL", "gpt-4o-mini")
        monkeypatch.setattr(sqa.settings, "QA_AUDITOR_MODEL", "gpt-4o")
        assert sqa.qa_model() == "gpt-4o-mini"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
