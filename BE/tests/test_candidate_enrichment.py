"""Candidate enrichment — selection-size chunking (no artificial cap).

The job-candidates "Enrich" action used to hard-reject any selection over 10
before queuing anything (JOB_ENRICH_SELECTION_MAX). That cap is gone — the
recruiter enriches however many candidates they manually selected. The real
vendor-side guard, APIFY_ENRICH_MAX, still protects a SINGLE Apify call from
becoming oversized (or refused outright); `enrich_candidates` now chunks the
fetch into APIFY_ENRICH_MAX-sized groups so a large selection goes through as
several safe calls instead of one that exceeds the guard.

Offline: Mongo and Apify are both stubs.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest
from bson import ObjectId

from app.services.sourcing import candidate_enrichment as ce


class _FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakeCandidatesCol:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = {d["_id"]: d for d in docs}
        self.updates: List[Dict[str, Any]] = []

    def find(self, query: Dict[str, Any]):
        ids = set((query.get("_id") or {}).get("$in", []))
        return _FakeCursor([d for oid, d in self._docs.items() if oid in ids])

    async def update_one(self, flt, update):
        self.updates.append({"filter": flt, "update": update})


class _FakeApifyService:
    """Records the size of every `enrich_profiles` call it receives."""
    def __init__(self):
        self.calls: List[int] = []

    def enrich_profiles(self, identifiers: List[str]) -> Dict[str, Dict[str, Any]]:
        self.calls.append(len(identifiers))
        return {ident: {"headline": "Something", "experience": []} for ident in identifiers}


@pytest.mark.asyncio
async def test_large_selection_is_chunked_at_apify_enrich_max(monkeypatch):
    n = 37
    docs = [
        {
            "_id": ObjectId(),
            "externalLinkedinUrl": f"https://www.linkedin.com/in/person-{i}",
            "isApifyEnriched": False,
        }
        for i in range(n)
    ]
    col = _FakeCandidatesCol(docs)

    async def fake_get_collection(name):
        return col

    async def fake_cache_lookup(identifiers):
        return {}

    async def fake_cache_store(profiles):
        return None

    fake_service = _FakeApifyService()

    monkeypatch.setattr(ce, "get_collection", fake_get_collection)
    monkeypatch.setattr(ce, "_cache_lookup", fake_cache_lookup)
    monkeypatch.setattr(ce, "_cache_store", fake_cache_store)
    monkeypatch.setattr(ce, "get_apify_profile_service", lambda: fake_service)
    monkeypatch.setattr(ce.settings, "APIFY_ENRICH_MAX", 25)

    summary = await ce.enrich_candidates(candidate_ids=[str(d["_id"]) for d in docs])

    assert summary["enriched"] == n
    # No single call exceeded the guard, none were refused, and every
    # candidate was still reached — a selection of ANY size now goes through
    # as multiple safe calls instead of being capped or rejected up front.
    assert all(size <= 25 for size in fake_service.calls)
    assert sum(fake_service.calls) == n
    assert len(fake_service.calls) == 2  # 25 + 12


@pytest.mark.asyncio
async def test_small_selection_is_a_single_call(monkeypatch):
    n = 4
    docs = [
        {
            "_id": ObjectId(),
            "externalLinkedinUrl": f"https://www.linkedin.com/in/small-{i}",
            "isApifyEnriched": False,
        }
        for i in range(n)
    ]
    col = _FakeCandidatesCol(docs)

    async def fake_get_collection(name):
        return col

    async def fake_cache_lookup(identifiers):
        return {}

    async def fake_cache_store(profiles):
        return None

    fake_service = _FakeApifyService()

    monkeypatch.setattr(ce, "get_collection", fake_get_collection)
    monkeypatch.setattr(ce, "_cache_lookup", fake_cache_lookup)
    monkeypatch.setattr(ce, "_cache_store", fake_cache_store)
    monkeypatch.setattr(ce, "get_apify_profile_service", lambda: fake_service)
    monkeypatch.setattr(ce.settings, "APIFY_ENRICH_MAX", 25)

    summary = await ce.enrich_candidates(candidate_ids=[str(d["_id"]) for d in docs])

    assert summary["enriched"] == n
    assert fake_service.calls == [n]
