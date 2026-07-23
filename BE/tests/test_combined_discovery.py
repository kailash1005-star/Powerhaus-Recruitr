"""Unified (combined) discovery — Apify + Apollo run concurrently and merge.

Covers the new logic added for the one-screen dual-engine flow:

  * ``_norm_linkedin_url`` / ``_dedupe_cross_engine`` — a person found by BOTH
    engines (two rows keyed on different ids) collapses to ONE row, keeping the
    Apify row and carrying the Apollo id for a later contact reveal.
  * ``_combined_discover_for_job`` rollup — the shared ``searchStatus`` is
    derived from the two engines' outcomes (completed / awaiting_input / failed)
    and enrich readiness follows the Apify side.
  * Apollo ``search_people`` — no ``person_industries[]`` (not a real param), and
    the fallback cascade relaxes q_keywords → seniorities → location.
  * Apify ``_build_input`` — ``autoQuerySegmentation`` turns on only for a
    multi-page title/keyword search.

Offline: Mongo is a stub, the two engine workers and vendor calls are patched.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List

import pytest

from app.services import candidate_pipeline as cp


# ── URL normalisation ────────────────────────────────────────────────────────

class TestNormLinkedinUrl:
    def test_regional_hosts_and_scheme_collapse(self):
        a = cp._norm_linkedin_url("https://www.linkedin.com/in/Jane-Doe/")
        b = cp._norm_linkedin_url("http://de.linkedin.com/in/Jane-Doe")
        c = cp._norm_linkedin_url("linkedin.com/in/jane-doe/?trk=x#a")
        assert a == b == c == "linkedin.com/in/jane-doe"

    def test_empty_is_empty(self):
        assert cp._norm_linkedin_url(None) == ""
        assert cp._norm_linkedin_url("") == ""


# ── Cross-engine dedup ───────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, docs): self._docs = docs
    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakeDedupeCol:
    def __init__(self, docs):
        self._docs = docs
        self.updates: List[tuple] = []
        self.deletes: List[Dict[str, Any]] = []

    def find(self, flt, proj=None):
        return _FakeCursor([d for d in self._docs if d.get("externalLinkedinUrl")])

    async def update_one(self, flt, update):
        self.updates.append((flt, update))

    async def delete_many(self, flt):
        self.deletes.append(flt)


class TestDedupeCrossEngine:
    async def test_both_engines_collapse_to_one_apify_row(self, monkeypatch):
        docs = [
            {"_id": "a", "externalLinkedinUrl": "https://www.linkedin.com/in/jane/",
             "source": "apify_search", "apolloId": "URN:1", "sourceChannels": ["title"]},
            {"_id": "b", "externalLinkedinUrl": "https://de.linkedin.com/in/jane",
             "source": "apollo_search", "apolloId": "APOLLO_ID", "sourceChannels": []},
        ]
        col = _FakeDedupeCol(docs)

        async def fake_get_collection(name):
            return col
        monkeypatch.setattr(cp, "get_collection", fake_get_collection)

        merged = await cp._dedupe_cross_engine("p", "j")
        assert merged == 1
        # The Apify row (a) survives, annotated; the Apollo row (b) is deleted.
        assert len(col.updates) == 1
        flt, update = col.updates[0]
        assert flt["_id"] == "a"
        sets = update["$set"]
        assert sets["apolloPersonId"] == "APOLLO_ID"
        assert sets["alsoFoundVia"] == ["apollo_search"]
        assert col.deletes and col.deletes[0]["_id"]["$in"] == ["b"]

    async def test_no_shared_url_no_merge(self, monkeypatch):
        docs = [
            {"_id": "a", "externalLinkedinUrl": "https://linkedin.com/in/jane",
             "source": "apify_search", "apolloId": "URN", "sourceChannels": []},
            {"_id": "b", "externalLinkedinUrl": "https://linkedin.com/in/john",
             "source": "apollo_search", "apolloId": "AP", "sourceChannels": []},
        ]
        col = _FakeDedupeCol(docs)
        monkeypatch.setattr(cp, "get_collection", lambda name: _async(col))
        merged = await cp._dedupe_cross_engine("p", "j")
        assert merged == 0
        assert not col.updates and not col.deletes


async def _async(v):
    return v


# ── Combined runner rollup ───────────────────────────────────────────────────

def _patch_combined(monkeypatch, *, apify_ret, apollo_ret, job_total):
    """Patch the combined runner's collaborators; return a dict recording the
    rollup ``_finish`` and ``_set_enrich`` calls plus which engines ran."""
    rec: Dict[str, Any] = {"finish": None, "enrich": None, "ran": [], "qa": None}

    async def fake_claim(pipeline_id, job_id, run_apify, run_apollo):
        return True

    async def fake_apify(*a, **k):
        rec["ran"].append("apify")
        if isinstance(apify_ret, Exception):
            raise apify_ret
        return apify_ret

    async def fake_apollo(*a, **k):
        rec["ran"].append("apollo")
        if isinstance(apollo_ret, Exception):
            raise apollo_ret
        return apollo_ret

    async def fake_dedupe(pipeline_id, job_id):
        return 0

    async def fake_recount(pipeline_id):
        return {}

    async def fake_count(pipeline_id, job_id):
        return job_total

    async def fake_engine_errors(pipeline_id, job_id):
        return {"apify": None, "apollo": None}

    async def fake_qa(pipeline_id, job_id, apify_filters, apollo_filters):
        rec["qa"] = {"apify": apify_filters, "apollo": apollo_filters}

    async def fake_finish(pipeline_id, job_id, *, status, status_field="searchStatus", **extras):
        if status_field == "searchStatus":
            rec["finish"] = {"status": status, **extras}

    async def fake_set_enrich(pipeline_id, job_id, status, **extras):
        rec["enrich"] = {"status": status, **extras}

    @asynccontextmanager
    async def fake_ctx(*a, **k):
        yield

    from app.services import cost_service
    # These tests exercise the dual-engine rollup; Apollo search is disabled by
    # default in prod, so enable it here to drive both engines.
    from app.config import settings as _cfg
    monkeypatch.setattr(_cfg, "SOURCING_APOLLO_SEARCH_ENABLED", True, raising=False)
    monkeypatch.setattr(cp, "_claim_combined", fake_claim)
    monkeypatch.setattr(cp, "_discover_candidates_for_job", fake_apify)
    monkeypatch.setattr(cp, "_apollo_discover_for_job", fake_apollo)
    monkeypatch.setattr(cp, "_dedupe_cross_engine", fake_dedupe)
    monkeypatch.setattr(cp, "recount_pipeline", fake_recount)
    monkeypatch.setattr(cp, "_job_candidate_count", fake_count)
    monkeypatch.setattr(cp, "_engine_errors", fake_engine_errors)
    monkeypatch.setattr(cp, "_audit_combined_results", fake_qa)
    monkeypatch.setattr(cp, "_finish", fake_finish)
    monkeypatch.setattr(cp, "_set_enrich", fake_set_enrich)
    monkeypatch.setattr(cost_service, "cost_context", fake_ctx)
    return rec


class TestCombinedRollup:
    async def test_both_found_completes_and_enrich_ready(self, monkeypatch):
        rec = _patch_combined(monkeypatch, apify_ret=5, apollo_ret=7, job_total=11)
        await cp._combined_discover_for_job(
            "p", "j", {"currentJobTitles": ["X"]}, {"titles": ["X"]},
            {"apify": True, "apollo": True}, 25)
        assert set(rec["ran"]) == {"apify", "apollo"}
        assert rec["finish"]["status"] == "completed"
        assert rec["enrich"]["status"] == "ready" and rec["enrich"]["enrichReady"] == 5

    async def test_both_failed_rolls_up_failed(self, monkeypatch):
        rec = _patch_combined(monkeypatch, apify_ret=None, apollo_ret=None, job_total=0)
        await cp._combined_discover_for_job(
            "p", "j", {"currentJobTitles": ["X"]}, {"titles": ["X"]},
            {"apify": True, "apollo": True}, 25)
        assert rec["finish"]["status"] == "failed"
        assert "failed" in (rec["finish"].get("searchError") or "").lower()

    async def test_both_zero_is_awaiting_input(self, monkeypatch):
        rec = _patch_combined(monkeypatch, apify_ret=0, apollo_ret=0, job_total=0)
        await cp._combined_discover_for_job(
            "p", "j", {"currentJobTitles": ["X"]}, {"titles": ["X"]},
            {"apify": True, "apollo": True}, 25)
        assert rec["finish"]["status"] == "awaiting_input"
        assert rec["enrich"]["status"] == "none"

    async def test_apollo_off_skips_apollo(self, monkeypatch):
        rec = _patch_combined(monkeypatch, apify_ret=4, apollo_ret=9, job_total=4)
        await cp._combined_discover_for_job(
            "p", "j", {"currentJobTitles": ["X"]}, {"titles": ["X"]},
            {"apify": True, "apollo": False}, 25)
        assert rec["ran"] == ["apify"]  # Apollo never ran → no Apollo cost
        assert rec["finish"]["status"] == "completed"

    async def test_apollo_search_disabled_by_default(self, monkeypatch):
        # Product default: Apollo is NOT a search engine even when the caller
        # asks for it — LinkedIn (Apify) is the sole source. (This test does not
        # enable the flag, unlike the others.)
        rec = _patch_combined(monkeypatch, apify_ret=4, apollo_ret=9, job_total=4)
        from app.config import settings as _cfg
        monkeypatch.setattr(_cfg, "SOURCING_APOLLO_SEARCH_ENABLED", False, raising=False)
        await cp._combined_discover_for_job(
            "p", "j", {"currentJobTitles": ["X"]}, {"titles": ["X"]},
            {"apify": True, "apollo": True}, 25)
        assert rec["ran"] == ["apify"]  # Apollo refused despite engines.apollo=True
        assert rec["finish"]["status"] == "completed"

    async def test_one_engine_survives_other_crash(self, monkeypatch):
        rec = _patch_combined(
            monkeypatch, apify_ret=RuntimeError("boom"), apollo_ret=6, job_total=6)
        await cp._combined_discover_for_job(
            "p", "j", {"currentJobTitles": ["X"]}, {"titles": ["X"]},
            {"apify": True, "apollo": True}, 25)
        # Apify raised, Apollo found people → overall still completes.
        assert rec["finish"]["status"] == "completed"

    async def test_qa_audits_the_merged_set_on_success(self, monkeypatch):
        # The trust fix: a completed combined run QA-audits BOTH engines' output
        # in one pass — Apollo results are no longer published unverified.
        rec = _patch_combined(monkeypatch, apify_ret=5, apollo_ret=7, job_total=11)
        await cp._combined_discover_for_job(
            "p", "j", {"currentJobTitles": ["X"]}, {"titles": ["Y"]},
            {"apify": True, "apollo": True}, 25)
        assert rec["finish"]["status"] == "completed"
        assert rec["qa"] is not None, "combined QA never ran"
        # Both engines' filters reach the auditor.
        assert rec["qa"]["apify"] == {"currentJobTitles": ["X"]}
        assert rec["qa"]["apollo"] == {"titles": ["Y"]}

    async def test_qa_skipped_when_run_did_not_complete(self, monkeypatch):
        # Nothing to trust-check on a failed run — don't spend the QA LLM call.
        rec = _patch_combined(monkeypatch, apify_ret=None, apollo_ret=None, job_total=0)
        await cp._combined_discover_for_job(
            "p", "j", {"currentJobTitles": ["X"]}, {"titles": ["Y"]},
            {"apify": True, "apollo": True}, 25)
        assert rec["finish"]["status"] == "failed"
        assert rec["qa"] is None

    async def test_stale_total_does_not_mask_a_failed_run(self, monkeypatch):
        # The exact reported bug: Apollo off, Apify FAILED, but a leftover
        # candidate from an earlier run makes job_total > 0. Success is judged by
        # what THIS run added (0), not the cumulative total → the run is failed.
        rec = _patch_combined(monkeypatch, apify_ret=None, apollo_ret=0, job_total=1)
        await cp._combined_discover_for_job(
            "p", "j", {"currentJobTitles": ["X"]}, {"titles": ["X"]},
            {"apify": True, "apollo": False}, 25)
        assert rec["ran"] == ["apify"]
        assert rec["finish"]["status"] == "failed"

    async def test_quota_error_is_surfaced_distinctly(self, monkeypatch):
        rec = _patch_combined(monkeypatch, apify_ret=None, apollo_ret=None, job_total=0)

        async def quota_errors(pipeline_id, job_id):
            return {"apify": "Apify refused: 'free user run limit reached'.",
                    "apollo": None}
        monkeypatch.setattr(cp, "_engine_errors", quota_errors)

        await cp._combined_discover_for_job(
            "p", "j", {"currentJobTitles": ["X"]}, {"titles": ["X"]},
            {"apify": True, "apollo": True}, 25)
        assert rec["finish"]["status"] == "failed"
        assert rec["finish"]["searchQuotaHit"] is True
        assert "plan/credit limit" in rec["finish"]["searchError"]


# ── Keyword-channel policy: the executive/cofounder refusal ──────────────────

class TestChannelScreenPolicy:
    def _drop_verdict(self):
        return {"decision": "drop", "score": 0.0, "roleFit": 0.0,
                "reasons": ["Title shares no vocabulary with this role."]}

    def test_executive_keyword_hit_is_refused(self):
        # An "Owner" found only by the fuzzy keyword channel must NOT be rescued
        # — this is the "cofounder shows up as an SAP consultant" leak.
        keep, v = cp._channel_screen_policy(
            False, self._drop_verdict(), ["keyword"], title="Owner")
        assert keep is False
        assert v["decision"] == "drop"
        assert "executive" in v["reasons"][0].lower()

    def test_non_executive_keyword_hit_is_still_rescued(self):
        # The legit case: an IC title the title-gate couldn't score, whose
        # profile text matched — kept for enrichment.
        keep, v = cp._channel_screen_policy(
            False, self._drop_verdict(), ["keyword"], title="IT-Consultant bei X")
        assert keep is True
        assert v["decision"] == "keep"
        assert v["score"] >= 30.0

    def test_title_channel_drop_is_not_rescued(self):
        # No keyword channel → a drop stays a drop regardless of title.
        keep, v = cp._channel_screen_policy(
            False, self._drop_verdict(), ["title"], title="Owner")
        assert keep is False


# ── Apollo people-search: no person_industries[] + fallback cascade ──────────

class TestApolloSearchPeople:
    def _svc_recording(self, monkeypatch, *, win_stage: int):
        """An ApolloService whose paged search records params and returns people
        only at ``win_stage`` (0-based)."""
        from app.services.apollo_service import ApolloService
        svc = ApolloService(api_key="k")
        calls: List[dict] = []

        def fake_paged(params, label, max_results):
            calls.append(params)
            return [{"id": "1"}] if (len(calls) - 1) == win_stage else []

        monkeypatch.setattr(svc, "_paged_search_capped", fake_paged)
        return svc, calls

    def test_industries_never_sent_as_person_industries(self, monkeypatch):
        svc, calls = self._svc_recording(monkeypatch, win_stage=0)
        out = svc.search_people(
            titles=["SAP EWM Consultant"], locations=["Germany"],
            skills=["SAP EWM"], industries=["Information Technology"])
        assert out["people"]
        # No call ever carries the bogus person_industries[] param…
        assert all("person_industries[]" not in c for c in calls)
        # …and the industry term rides along in q_keywords instead.
        assert "Information Technology" in calls[0]["q_keywords"]

    def test_fallback_drops_keywords_then_returns(self, monkeypatch):
        # Stage 0 (with q_keywords) → empty; stage 1 (no q_keywords) → people.
        svc, calls = self._svc_recording(monkeypatch, win_stage=1)
        out = svc.search_people(
            titles=["SAP EWM Consultant"], skills=["SAP EWM"],
            seniorities=["senior"], locations=["Germany"])
        assert out["people"] and out["applied_fallback_stage"] == 1
        assert "q_keywords" in calls[0] and "q_keywords" not in calls[1]

    def test_similar_titles_always_on(self, monkeypatch):
        svc, calls = self._svc_recording(monkeypatch, win_stage=0)
        svc.search_people(titles=["SAP EWM Consultant"])
        assert calls[0]["include_similar_titles"] == "true"

    def test_no_titles_no_keywords_returns_empty(self):
        from app.services.apollo_service import ApolloService
        out = ApolloService(api_key="k").search_people(titles=[], skills=[])
        assert out == {"people": [], "params_used": {}}


# ── Apify _build_input: autoQuerySegmentation gating ─────────────────────────

class TestApifySegmentation:
    def test_on_for_multipage_title_search(self):
        from app.services.apify_search_service import _build_input
        ri = _build_input({"currentJobTitles": ["SAP EWM Consultant"]}, 50)
        assert ri.get("autoQuerySegmentation") is True

    def test_off_for_single_page(self):
        from app.services.apify_search_service import _build_input
        ri = _build_input({"currentJobTitles": ["SAP EWM Consultant"]}, 25)
        assert "autoQuerySegmentation" not in ri

    def test_off_when_nothing_to_search(self):
        from app.services.apify_search_service import _build_input
        ri = _build_input({"locations": ["Germany"]}, 100)
        assert "autoQuerySegmentation" not in ri
