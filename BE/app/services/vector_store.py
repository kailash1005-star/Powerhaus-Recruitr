"""
Vector Store abstraction — swappable backend for candidate retrieval.

Three implementations behind one interface:
  * MongoVectorStore (DEFAULT) — brute-force cosine over vectors stored on the
    `cv_candidates` docs. Zero extra infra; exact; correct and fast for the demo
    / test corpus (and fine into the low thousands).
  * AtlasVectorStore — MongoDB Atlas native $vectorSearch (HNSW ANN) + $search
    (Lucene BM25). The PRODUCTION path at thousands+ of CVs: the brute-force
    backend loads every vector into memory per query and the lexical channel
    rebuilds a BM25 index from the whole corpus per query — both O(corpus) per
    run. Atlas moves both into persistent server-side indexes over the SAME
    vectors already stored on the docs (no re-embedding, no data migration).
  * PineconeVectorStore — external ANN service for non-Atlas deployments.

The matching service is written against this interface, so switching backends is
a one-line config change (VECTOR_BACKEND) with no code changes. A backend may
also expose `lexical_query(...)` (Atlas does) to serve the BM25 channel from a
persistent index instead of the per-query rebuild; the matcher uses it when
present and falls back to the in-process BM25 otherwise.

Retrieval is pure similarity (broad recall); hard constraints (years, location,
must-have skills) are applied deterministically afterwards in matching_service.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


# ── Mongo brute-force backend ────────────────────────────────────────────────
class MongoVectorStore:
    """Cosine similarity over vectors persisted on `cv_candidates.embedding.vector`."""

    def __init__(self, db):
        self._col = db["cv_candidates"]

    async def upsert(self, items: List[dict]) -> None:
        # No-op: ingestion already persists `embedding.vector` on each doc, which
        # is the source of truth for this backend.
        return None

    async def query(
        self, vector: List[float], top_k: int, _filters: Optional[dict] = None
    ) -> List[Tuple[str, float]]:
        """Rank candidates by MAX cosine over doc vector + chunk vectors.

        Max, not mean: a JD should match the most relevant PART of a CV. A
        payroll JD against a CV whose middle third is payroll should score that
        third, not the average of payroll with two unrelated roles — averaging
        is exactly the dilution that made long CVs rank under thin ones.
        Docs ingested before chunking existed have no chunks and rank by their
        doc vector alone, unchanged.
        """
        import numpy as np

        q = np.asarray(vector, dtype="float32")
        qn = np.linalg.norm(q)
        if qn == 0:
            return []
        q = q / qn

        ids: List[str] = []
        best: List[float] = []
        cursor = self._col.find(
            {"status": "embedded", "embedding.vector": {"$exists": True, "$ne": None}},
            {"embedding.vector": 1, "chunks.vector": 1},
        )
        async for doc in cursor:
            vecs: List[List[float]] = []
            dv = (doc.get("embedding") or {}).get("vector")
            if dv:
                vecs.append(dv)
            for ch in (doc.get("chunks") or []):
                cv = (ch or {}).get("vector")
                if cv:
                    vecs.append(cv)
            if not vecs:
                continue
            m = np.asarray(vecs, dtype="float32")
            norms = np.linalg.norm(m, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            sims = (m / norms) @ q
            ids.append(str(doc["_id"]))
            best.append(float(sims.max()))

        if not ids:
            return []
        order = sorted(range(len(ids)), key=lambda i: (-best[i], ids[i]))[:top_k]
        return [(ids[i], best[i]) for i in order]


# ── Atlas native backend ─────────────────────────────────────────────────────
class AtlasVectorStore:
    """MongoDB Atlas $vectorSearch (HNSW ANN) + $search (BM25) over cv_candidates.

    Same vectors as MongoVectorStore — they already live on the docs — but read
    through a persistent server-side index instead of being streamed into memory
    each query. Two vector paths are indexed and queried:
      * chunks.vector   — the per-chunk vectors (Atlas returns each doc once with
                          its BEST-matching chunk score → the same MAX-over-chunks
                          semantics the Mongo backend computes by hand);
      * embedding.vector — the doc-level vector, so legacy docs ingested before
                          chunking (no chunks[]) are still retrievable.
    The two result sets are max-merged per candidate, mirroring MongoVectorStore.

    Score convention: Atlas returns vectorSearchScore = (1 + cosine) / 2 for a
    cosine index. We convert back to RAW cosine (2*score − 1) so the semantic
    value handed to the scorer is on the exact scale MongoVectorStore returns —
    scores stay identical across backends and SCORING_VERSION is preserved.
    """

    def __init__(self, db):
        self._col = db["cv_candidates"]
        self._vindex = settings.ATLAS_VECTOR_INDEX
        self._sindex = settings.ATLAS_SEARCH_INDEX
        self._mult = max(1, int(settings.ATLAS_NUM_CANDIDATES_MULT or 20))

    async def upsert(self, items: List[dict]) -> None:
        # No-op: like the Mongo backend, ingestion persists the vectors on the
        # docs and the Atlas index reads them in place. Nothing to push.
        return None

    async def _search_path(
        self, path: str, vector: List[float], top_k: int, num_candidates: int
    ) -> List[Tuple[str, float]]:
        pipeline = [
            {
                "$vectorSearch": {
                    "index": self._vindex,
                    "path": path,
                    "queryVector": vector,
                    "numCandidates": num_candidates,
                    "limit": top_k,
                    "filter": {"status": {"$eq": "embedded"}},
                }
            },
            {"$project": {"_id": 1, "score": {"$meta": "vectorSearchScore"}}},
        ]
        out: List[Tuple[str, float]] = []
        async for d in self._col.aggregate(pipeline):
            # (1 + cosine)/2  →  raw cosine, to match MongoVectorStore's scale.
            out.append((str(d["_id"]), 2.0 * float(d.get("score", 0.0)) - 1.0))
        return out

    async def query(
        self, vector: List[float], top_k: int, _filters: Optional[dict] = None
    ) -> List[Tuple[str, float]]:
        num_candidates = max(top_k * self._mult, top_k)
        best: dict = {}
        for path in ("chunks.vector", "embedding.vector"):
            try:
                hits = await self._search_path(path, vector, top_k, num_candidates)
            except Exception:
                # A path may be absent from the index (e.g. no legacy docs) — a
                # missing path must not sink the whole query, the other still ran.
                logger.warning("[Atlas] $vectorSearch on %s failed", path, exc_info=True)
                continue
            for cid, score in hits:
                if score > best.get(cid, float("-inf")):
                    best[cid] = score
        ranked = sorted(best.items(), key=lambda x: (-x[1], x[0]))
        return ranked[:top_k]

    async def lexical_query(self, query_text: str, top_k: int) -> List[Tuple[str, float]]:
        """BM25 channel served from the persistent Atlas $search index.

        Replaces the per-query in-process BM25 rebuild (which re-tokenises the
        whole corpus every run — O(corpus) per query). Returns (id, searchScore);
        only rank order matters downstream (RRF fusion), not the score scale.
        """
        query_text = (query_text or "").strip()
        if not query_text:
            return []
        pipeline = [
            {
                "$search": {
                    "index": self._sindex,
                    "text": {
                        "query": query_text,
                        "path": ["markdown", "profile.skills", "profile.currentTitle"],
                    },
                }
            },
            {"$match": {"status": "embedded"}},
            {"$limit": top_k},
            {"$project": {"_id": 1, "score": {"$meta": "searchScore"}}},
        ]
        out: List[Tuple[str, float]] = []
        try:
            async for d in self._col.aggregate(pipeline):
                out.append((str(d["_id"]), float(d.get("score", 0.0))))
        except Exception:
            logger.warning("[Atlas] $search lexical query failed", exc_info=True)
            return []
        return out


# ── Pinecone backend ─────────────────────────────────────────────────────────
class PineconeVectorStore:
    """Pinecone serverless index. Vectors carry candidateId as the id."""

    def __init__(self):
        if not settings.PINECONE_API_KEY:
            raise RuntimeError("PINECONE_API_KEY not set but VECTOR_BACKEND=pinecone.")
        from pinecone import Pinecone
        self._pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        self._index = self._pc.Index(settings.PINECONE_INDEX)
        self._ns = settings.PINECONE_NAMESPACE

    async def upsert(self, items: List[dict]) -> None:
        import asyncio
        if not items:
            return
        vectors = [
            {"id": it["id"], "values": it["vector"], "metadata": it.get("metadata", {})}
            for it in items
        ]
        await asyncio.to_thread(self._index.upsert, vectors=vectors, namespace=self._ns)

    async def query(
        self, vector: List[float], top_k: int, filters: Optional[dict] = None
    ) -> List[Tuple[str, float]]:
        import asyncio
        res = await asyncio.to_thread(
            self._index.query,
            vector=vector,
            top_k=top_k,
            namespace=self._ns,
            include_values=False,
            include_metadata=False,
            filter=filters or None,
        )
        matches = res.get("matches", []) if isinstance(res, dict) else getattr(res, "matches", [])
        # Chunk vectors are stored as "<candidateId>#c<i>" — fold every chunk hit
        # back onto its candidate, keeping the best score (max over chunks+doc,
        # mirroring the Mongo backend's semantics).
        best: dict = {}
        for m in matches:
            mid = str(m["id"] if isinstance(m, dict) else m.id).split("#", 1)[0]
            score = float(m["score"] if isinstance(m, dict) else m.score)
            if score > best.get(mid, float("-inf")):
                best[mid] = score
        out: List[Tuple[str, float]] = sorted(best.items(), key=lambda x: (-x[1], x[0]))
        return out[:top_k]


def get_vector_store(db):
    """Factory — returns the configured backend."""
    backend = (settings.VECTOR_BACKEND or "mongo").lower()
    if backend == "pinecone":
        return PineconeVectorStore()
    if backend == "atlas":
        return AtlasVectorStore(db)
    return MongoVectorStore(db)


# ── Atlas index provisioning ─────────────────────────────────────────────────
# Definitions of the two Atlas Search indexes the AtlasVectorStore reads. Applied
# best-effort at startup when VECTOR_BACKEND=atlas; also usable as the source of
# truth for creating them by hand in the Atlas UI. Requires an Atlas M10+ tier —
# search indexes are unavailable on shared (M0/M2/M5) clusters.
_ATLAS_VECTOR_INDEX_DEF = {
    "fields": [
        # Cosine over the 1536-dim text-embedding-3-small vectors, both paths.
        {"type": "vector", "path": "embedding.vector", "numDimensions": 1536, "similarity": "cosine"},
        {"type": "vector", "path": "chunks.vector", "numDimensions": 1536, "similarity": "cosine"},
        # Pre-filter so ANN only ever considers embedded (matchable) docs.
        {"type": "filter", "path": "status"},
    ]
}
_ATLAS_SEARCH_INDEX_DEF = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "markdown": {"type": "string"},
            "status": {"type": "token"},
            "profile": {
                "type": "document",
                "fields": {
                    "skills": {"type": "string"},
                    "currentTitle": {"type": "string"},
                },
            },
        },
    }
}


async def ensure_atlas_indexes(db) -> None:
    """Create the vector + lexical search indexes if missing (idempotent).

    Best-effort: logs and returns on any failure (shared-tier cluster, missing
    privileges, index already building). Atlas builds search indexes
    asynchronously, so a freshly-created index may be a few minutes from
    queryable — until then AtlasVectorStore.query simply returns fewer/no hits.
    """
    if (settings.VECTOR_BACKEND or "mongo").lower() != "atlas":
        return
    col = db["cv_candidates"]
    try:
        existing = {ix["name"] async for ix in await col.list_search_indexes()}
    except Exception:
        # Driver/tier without search-index listing — try to create anyway; a
        # duplicate-create raises and is swallowed per-index below.
        existing = set()
    wanted = [
        (settings.ATLAS_VECTOR_INDEX, "vectorSearch", _ATLAS_VECTOR_INDEX_DEF),
        (settings.ATLAS_SEARCH_INDEX, "search", _ATLAS_SEARCH_INDEX_DEF),
    ]
    for name, kind, definition in wanted:
        if name in existing:
            continue
        try:
            await col.create_search_index(
                {"name": name, "type": kind, "definition": definition}
            )
            logger.info("[Atlas] creating %s index %r (builds asynchronously)", kind, name)
        except Exception:
            logger.warning("[Atlas] could not create %s index %r", kind, name, exc_info=True)
