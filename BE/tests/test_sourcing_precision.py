"""Sourcing-precision guarantees (FC-23..FC-27).

The contract under test, end to end:

  1. Widening a search may NEVER change the target profession. The Broadener's
     titles/query are clamped in code (lock_target); the domain guard that
     protects every other title path is two-tier — an ecosystem brand ("SAP")
     alone never proves a title is in-domain, only a CORE specialization term
     ("HCM") does. This is the exact bug the customer hit: an SAP-HCM search
     drifting into SAP FI-CO because both share "SAP".

  2. Recall comes from running MORE channels on the SAME specialty (title +
     keyword, merged and deduped), never from loosening the specialty.

  3. The prescreen gate is channel-aware: a keyword-channel hit is kept even
     when its title alone can't evidence the role, and a hit corroborated by
     both channels outranks a single-channel one.

All tests are offline — no Apify, no LLM, no Mongo.
"""
from __future__ import annotations

import pytest

from app.services.sourcing.broadener import (
    _domain_anchor, _enforce_domain, lock_target, next_attempt,
)
from app.services.sourcing.common import derive_anchor_terms, title_in_domain
from app.services.sourcing.models import (
    BroadenDecision, BroadeningStep, DomainAnchor, SearchAttempt, SearchBrief,
    SearchFilters, SearchStrategy,
)
from app.services.sourcing.strategist import _sanitize


def _attempt(titles, query="", n=1, **kw):
    return SearchAttempt(
        attempt=n, action="initial" if n == 1 else "broaden",
        filters={"currentJobTitles": titles, "searchQuery": query, **kw},
        resultCount=0,
    )


# ── Two-tier anchor ──────────────────────────────────────────────────────────

class TestDomainAnchor:
    def test_ecosystem_brand_is_not_core(self):
        core, eco = derive_anchor_terms(["SAP HCM Consultant", "SAP HR Consultant"])
        assert "sap" in eco and "sap" not in core
        assert "hcm" in core

    def test_fico_fails_hcm_anchor(self):
        """THE customer bug: FI-CO must not pass as in-domain for an HCM role."""
        core, _ = derive_anchor_terms(["SAP HCM Consultant"])
        assert not title_in_domain("SAP FICO Consultant", core)
        assert not title_in_domain("SAP Application Manager", core)
        assert title_in_domain("SAP HCM Berater", core)

    def test_brand_only_domain_keeps_brand_as_core(self):
        """'SAP Consultant' alone: the brand IS the only signal — keep it."""
        core, eco = derive_anchor_terms(["SAP Consultant"])
        assert core == ["sap"] and eco == []

    def test_declared_anchor_wins_over_heuristic(self):
        anchor = _domain_anchor(
            [_attempt(["SAP HCM Consultant"])],
            strategy_anchor={"coreTerms": ["successfactors", "payroll"],
                             "ecosystemTerms": ["sap"]},
        )
        assert anchor == ["payroll", "successfactors"]

    def test_anchor_from_brief_when_no_titles(self):
        anchor = _domain_anchor(
            [_attempt([], query="SAP HCM")],
            brief=SearchBrief(jobTitle="SAP HCM Consultant"),
        )
        assert "hcm" in anchor and "sap" not in anchor


# ── The Broadener cannot change the target ───────────────────────────────────

class TestLockTarget:
    def test_titles_and_query_clamped(self):
        attempts = [_attempt(["SAP HCM Consultant", "SAP HR Consultant"], "SAP HCM")]
        drifted = BroadenDecision(
            action="generalise_titles", reasoning="",
            filters=SearchFilters(currentJobTitles=["SAP Consultant"],
                                  searchQuery="SAP", locations=["Germany"]),
        )
        locked = lock_target(drifted, attempts)
        assert locked.filters.currentJobTitles == ["SAP HCM Consultant", "SAP HR Consultant"]
        assert locked.filters.searchQuery == "SAP HCM"
        assert locked.filters.locations == ["Germany"]  # non-target dims survive

    async def test_ladder_fallback_is_clamped_too(self, monkeypatch):
        """A legacy persisted ladder with a generalise_titles step must run with
        the ORIGINAL titles — only its other relaxations apply."""
        monkeypatch.setattr(
            "app.services.sourcing.broadener.llm_available", lambda: False)
        attempts = [_attempt(["SAP HCM Consultant"], "SAP HCM", seniorityLevel="120")]
        ladder = [BroadeningStep(
            step=1, action="generalise_titles", detail="",
            filters=SearchFilters(currentJobTitles=["SAP Consultant"],
                                  searchQuery="SAP HCM"),
        )]
        decision = await next_attempt(SearchBrief(jobTitle="SAP HCM Consultant"),
                                      attempts, ladder)
        assert decision is not None
        assert decision.filters.currentJobTitles == ["SAP HCM Consultant"]

    async def test_exhausted_ladder_stops(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.sourcing.broadener.llm_available", lambda: False)
        decision = await next_attempt(
            SearchBrief(jobTitle="X"), [_attempt(["X Specialist"])], ladder=[])
        assert decision is None


class TestEnforceDomain:
    def test_off_domain_titles_stripped(self):
        attempts = [_attempt(["SAP HCM Consultant"])]
        d = BroadenDecision(action="a", reasoning="", filters=SearchFilters(
            currentJobTitles=["SAP FICO Consultant", "SAP HCM Berater"]))
        out = _enforce_domain(d, attempts,
                              strategy_anchor={"coreTerms": ["hcm"]})
        assert out is not None
        assert out.filters.currentJobTitles == ["SAP HCM Berater"]

    def test_total_drift_returns_none(self):
        attempts = [_attempt(["SAP HCM Consultant"])]
        d = BroadenDecision(action="a", reasoning="", filters=SearchFilters(
            currentJobTitles=["SAP FICO Consultant", "SAP Basis Administrator"]))
        assert _enforce_domain(d, attempts,
                               strategy_anchor={"coreTerms": ["hcm"]}) is None


# ── Dual-channel merge ───────────────────────────────────────────────────────

class TestSearchChannels:
    async def test_merge_dedupe_and_corroboration(self, monkeypatch):
        from app.services import candidate_pipeline as cp

        calls = []

        async def fake_run_search(pid, jid, filters, max_items):
            calls.append(dict(filters))
            if filters.get("currentJobTitles"):
                return [{"profileId": "a", "currentTitle": "SAP HCM Consultant"},
                        {"profileId": "b", "currentTitle": "SAP HR Consultant"}]
            return [{"profileId": "b", "currentTitle": "SAP HR Consultant"},
                    {"profileId": "c", "currentTitle": "IT-Consultant"}]

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        profiles, counts = await cp._run_search_channels(
            "p", "j",
            {"currentJobTitles": ["SAP HCM Consultant"], "searchQuery": "SAP HCM",
             "locations": ["Germany"]},
            25, include_keyword_channel=True,
        )
        assert counts == {"title": 2, "keyword": 2}
        by_id = {p["profileId"]: p for p in profiles}
        assert len(profiles) == 3                          # deduped
        assert by_id["a"]["channels"] == ["title"]
        assert by_id["b"]["channels"] == ["title", "keyword"]  # corroborated
        assert by_id["c"]["channels"] == ["keyword"]
        # The keyword channel must NOT carry the title filter.
        assert all("currentJobTitles" not in c or not c.get("currentJobTitles")
                   for c in calls[1:])

    async def test_keyword_channel_failure_is_not_fatal(self, monkeypatch):
        from app.services import candidate_pipeline as cp

        async def fake_run_search(pid, jid, filters, max_items):
            if filters.get("currentJobTitles"):
                return [{"profileId": "a", "currentTitle": "T"}]
            raise RuntimeError("keyword page exploded")

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        profiles, counts = await cp._run_search_channels(
            "p", "j",
            {"currentJobTitles": ["T"], "searchQuery": "T kw"},
            25, include_keyword_channel=True,
        )
        assert [p["profileId"] for p in profiles] == ["a"]
        assert counts["keyword"] == 0

    async def test_retries_skip_keyword_channel(self, monkeypatch):
        from app.services import candidate_pipeline as cp
        n = {"calls": 0}

        async def fake_run_search(pid, jid, filters, max_items):
            n["calls"] += 1
            return []

        monkeypatch.setattr(cp, "_run_search", fake_run_search)
        await cp._run_search_channels(
            "p", "j", {"currentJobTitles": ["T"], "searchQuery": "q"},
            25, include_keyword_channel=False,
        )
        assert n["calls"] == 1


# ── Strategist sanitize: anchor hygiene + adjacent titles ────────────────────

class TestStrategistSanitize:
    def _strategy(self, **kw):
        base = dict(
            interpretedRole="SAP HCM Consultant",
            filters=SearchFilters(
                searchQuery="SAP HCM",
                currentJobTitles=["SAP HCM Consultant", "SAP SuccessFactors Consultant"],
            ),
            domainAnchor=DomainAnchor(coreTerms=["hcm", "successfactors"],
                                      ecosystemTerms=["sap"]),
        )
        base.update(kw)
        return SearchStrategy(**base)

    def test_generic_core_terms_stripped(self):
        s = self._strategy(domainAnchor=DomainAnchor(
            coreTerms=["hcm", "Consultant", "Manager"], ecosystemTerms=[]))
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        assert out.domainAnchor.coreTerms == ["hcm"]

    def test_empty_anchor_derived_from_titles(self):
        s = self._strategy(domainAnchor=DomainAnchor())
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        assert "hcm" in out.domainAnchor.coreTerms
        assert "sap" in out.domainAnchor.ecosystemTerms

    def test_anchor_rejecting_own_titles_is_rebuilt(self):
        s = self._strategy(domainAnchor=DomainAnchor(
            coreTerms=["entgeltabrechnung"], ecosystemTerms=[]))
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        assert "hcm" in out.domainAnchor.coreTerms

    def test_adjacent_titles_deduped_and_capped(self):
        s = self._strategy(adjacentTitles=[
            "SAP HCM Consultant",   # dupe of a current title → dropped
            "HRIS Consultant", "Workday HCM Consultant", "  ", "A", "B", "C", "D", "E",
        ])
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        assert "SAP HCM Consultant" not in out.adjacentTitles
        assert "HRIS Consultant" in out.adjacentTitles
        assert len(out.adjacentTitles) <= 6

    def test_ladder_titles_locked(self):
        s = self._strategy(broadeningLadder=[BroadeningStep(
            step=1, action="generalise_titles", detail="",
            filters=SearchFilters(currentJobTitles=["SAP Consultant"],
                                  searchQuery="SAP"),
        )])
        out = _sanitize(s, SearchBrief(jobTitle="SAP HCM Consultant"))
        step = out.broadeningLadder[0]
        assert step.filters.currentJobTitles == out.filters.currentJobTitles
        assert step.filters.searchQuery == out.filters.searchQuery


# ── Channel-aware prescreen policy ───────────────────────────────────────────

class TestChannelScreenPolicy:
    def test_keyword_hit_survives_title_only_gate(self):
        from app.services.candidate_pipeline import _channel_screen_policy
        keep, verdict = _channel_screen_policy(
            False, {"score": 0.0, "decision": "drop", "reasons": ["no overlap"]},
            ["keyword"],
        )
        assert keep is True
        assert verdict["decision"] == "keep"
        assert verdict["score"] >= 30.0

    def test_title_only_drop_stays_dropped(self):
        from app.services.candidate_pipeline import _channel_screen_policy
        keep, verdict = _channel_screen_policy(
            False, {"score": 0.0, "decision": "drop", "reasons": ["no overlap"]},
            ["title"],
        )
        assert keep is False

    def test_corroborated_hit_gets_rank_bonus(self):
        from app.services.candidate_pipeline import _channel_screen_policy
        keep, verdict = _channel_screen_policy(
            True, {"score": 80.0, "decision": "keep", "reasons": []},
            ["title", "keyword"],
        )
        assert keep and verdict["score"] == 85.0

    def test_bonus_capped(self):
        from app.services.candidate_pipeline import _channel_screen_policy
        _, verdict = _channel_screen_policy(
            True, {"score": 93.0, "decision": "keep", "reasons": []},
            ["title", "keyword"],
        )
        assert verdict["score"] == 95.0


# ── Enrichment cap ───────────────────────────────────────────────────────────

class TestEnrichCap:
    async def test_over_cap_is_rejected_with_400(self):
        from fastapi import HTTPException
        from app.api.v1.pipelines import BulkEnrichSchema, enrich_job_candidates

        body = BulkEnrichSchema(candidateIds=[f"c{i}" for i in range(11)])
        with pytest.raises(HTTPException) as exc:
            await enrich_job_candidates("p", "j", body)
        assert exc.value.status_code == 400
        assert "capped at 10" in exc.value.detail
