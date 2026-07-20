"""Evals for the Atlas ANN backend (production scale, VECTOR_BACKEND=atlas).

These pin the two properties that let the Atlas backend be a drop-in for the
brute-force Mongo backend WITHOUT shifting any score:

  * TestFactory — VECTOR_BACKEND routes to the right store; "atlas" gives the
    Atlas backend, unknown/blank falls back to Mongo (never crashes a run).
  * TestScoreConvention — Atlas returns vectorSearchScore = (1 + cosine)/2 for a
    cosine index; the backend MUST convert it back to raw cosine so the semantic
    value handed to the scorer is on the exact scale MongoVectorStore returns.
    If this drifts, SCORING_VERSION silently changes meaning across backends.
  * TestMaxMergeOverPaths — chunk-path and doc-path hits for the same candidate
    are merged by MAX (mirroring the Mongo backend's max-over-chunks), and a
    single failing path never sinks the whole query.
  * TestLexicalQuery — the persistent BM25 channel maps $search results to
    (id, score) and treats empty queries / failures as "no hits", not errors.

All offline: no Atlas, no Mongo, no network — a fake collection replays what
Atlas aggregation would yield.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.config import settings
from app.services import vector_store as vs


class _FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeCollection:
    """Replays Atlas aggregation output by inspecting the pipeline's first stage.

    `vector_hits[path]` → docs for a $vectorSearch on that path.
    `lexical_hits`      → docs for a $search.
    A path listed in `fail_paths` raises, to exercise the per-path guard.
    """

    def __init__(self, vector_hits=None, lexical_hits=None, fail_paths=()):
        self._vector_hits = vector_hits or {}
        self._lexical_hits = lexical_hits or []
        self._fail_paths = set(fail_paths)

    def aggregate(self, pipeline):
        first = pipeline[0]
        if "$vectorSearch" in first:
            path = first["$vectorSearch"]["path"]
            if path in self._fail_paths:
                raise RuntimeError(f"index missing path {path}")
            return _FakeCursor(self._vector_hits.get(path, []))
        if "$search" in first:
            return _FakeCursor(self._lexical_hits)
        return _FakeCursor([])


def _atlas_store(col) -> vs.AtlasVectorStore:
    store = vs.AtlasVectorStore.__new__(vs.AtlasVectorStore)
    store._col = col
    store._vindex = "cv_vectors"
    store._sindex = "cv_lexical"
    store._mult = 20
    return store


# ── Factory ──────────────────────────────────────────────────────────────────

class TestFactory:
    def test_atlas_backend_selected(self, monkeypatch):
        monkeypatch.setattr(settings, "VECTOR_BACKEND", "atlas")
        assert isinstance(vs.get_vector_store(db={"cv_candidates": object()}),
                          vs.AtlasVectorStore)

    def test_blank_falls_back_to_mongo(self, monkeypatch):
        monkeypatch.setattr(settings, "VECTOR_BACKEND", "")
        assert isinstance(vs.get_vector_store(db={"cv_candidates": object()}),
                          vs.MongoVectorStore)


# ── Score convention ───────────────────────────────────────────────────────────

class TestScoreConvention:
    @pytest.mark.asyncio
    async def test_atlas_score_converted_to_raw_cosine(self):
        # Atlas cosine score 0.75 ⇒ raw cosine 0.5; 0.5 ⇒ 0.0; 1.0 ⇒ 1.0.
        col = _FakeCollection(vector_hits={
            "chunks.vector": [{"_id": "a", "score": 0.75}],
            "embedding.vector": [],
        })
        hits = await _atlas_store(col).query([0.1] * 4, top_k=5)
        assert hits == [("a", pytest.approx(0.5))]

    @pytest.mark.asyncio
    async def test_matches_mongo_scale_bounds(self):
        col = _FakeCollection(vector_hits={
            "chunks.vector": [{"_id": "hi", "score": 1.0}, {"_id": "mid", "score": 0.5}],
            "embedding.vector": [],
        })
        hits = dict(await _atlas_store(col).query([0.1] * 4, top_k=5))
        assert hits["hi"] == pytest.approx(1.0)   # identical vectors → cosine 1
        assert hits["mid"] == pytest.approx(0.0)   # orthogonal → cosine 0


# ── Max-merge over the two vector paths ────────────────────────────────────────

class TestMaxMergeOverPaths:
    @pytest.mark.asyncio
    async def test_best_path_wins_per_candidate(self):
        # Candidate "x" scores better on its chunk than its doc vector — keep max.
        col = _FakeCollection(vector_hits={
            "chunks.vector": [{"_id": "x", "score": 0.9}],
            "embedding.vector": [{"_id": "x", "score": 0.6}],
        })
        hits = await _atlas_store(col).query([0.1] * 4, top_k=5)
        assert hits == [("x", pytest.approx(2 * 0.9 - 1))]

    @pytest.mark.asyncio
    async def test_failing_path_does_not_sink_query(self):
        # The doc-vector path is absent from the index; the chunk path still ranks.
        col = _FakeCollection(
            vector_hits={"chunks.vector": [{"_id": "x", "score": 0.8}]},
            fail_paths=["embedding.vector"],
        )
        hits = await _atlas_store(col).query([0.1] * 4, top_k=5)
        assert hits == [("x", pytest.approx(2 * 0.8 - 1))]

    @pytest.mark.asyncio
    async def test_results_sorted_desc_and_capped(self):
        col = _FakeCollection(vector_hits={
            "chunks.vector": [
                {"_id": "lo", "score": 0.55},
                {"_id": "hi", "score": 0.95},
                {"_id": "mid", "score": 0.75},
            ],
            "embedding.vector": [],
        })
        hits = await _atlas_store(col).query([0.1] * 4, top_k=2)
        assert [cid for cid, _ in hits] == ["hi", "mid"]


# ── Persistent lexical channel ─────────────────────────────────────────────────

class TestLexicalQuery:
    @pytest.mark.asyncio
    async def test_maps_search_scores(self):
        col = _FakeCollection(lexical_hits=[
            {"_id": "a", "score": 12.3}, {"_id": "b", "score": 4.1},
        ])
        hits = await _atlas_store(col).lexical_query("sap payroll", top_k=5)
        assert hits == [("a", pytest.approx(12.3)), ("b", pytest.approx(4.1))]

    @pytest.mark.asyncio
    async def test_empty_query_returns_no_hits(self):
        col = _FakeCollection(lexical_hits=[{"_id": "a", "score": 1.0}])
        assert await _atlas_store(col).lexical_query("   ", top_k=5) == []
