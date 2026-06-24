"""
Vector Store abstraction — swappable backend for candidate retrieval.

Two implementations behind one interface:
  * MongoVectorStore (DEFAULT) — brute-force cosine over vectors stored on the
    `cv_candidates` docs. Zero extra infra; correct and fast for the 50-CV demo
    (and fine into the low thousands).
  * PineconeVectorStore — for scale. Only imported when VECTOR_BACKEND=pinecone.

The matching service is written against this interface, so switching backends is
a one-line config change (VECTOR_BACKEND) with no code changes.

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
        import numpy as np

        q = np.asarray(vector, dtype="float32")
        qn = np.linalg.norm(q)
        if qn == 0:
            return []
        q = q / qn

        ids: List[str] = []
        mat: List[List[float]] = []
        cursor = self._col.find(
            {"status": "embedded", "embedding.vector": {"$exists": True, "$ne": None}},
            {"embedding.vector": 1},
        )
        async for doc in cursor:
            vec = (doc.get("embedding") or {}).get("vector")
            if vec:
                ids.append(str(doc["_id"]))
                mat.append(vec)

        if not mat:
            return []

        m = np.asarray(mat, dtype="float32")
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        m = m / norms
        sims = m @ q  # cosine similarity (both normalized)

        k = min(top_k, len(ids))
        top_idx = np.argpartition(-sims, k - 1)[:k]
        top_idx = top_idx[np.argsort(-sims[top_idx])]
        return [(ids[i], float(sims[i])) for i in top_idx]


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
        out: List[Tuple[str, float]] = []
        for m in matches:
            mid = m["id"] if isinstance(m, dict) else m.id
            score = m["score"] if isinstance(m, dict) else m.score
            out.append((str(mid), float(score)))
        return out


def get_vector_store(db):
    """Factory — returns the configured backend."""
    backend = (settings.VECTOR_BACKEND or "mongo").lower()
    if backend == "pinecone":
        return PineconeVectorStore()
    return MongoVectorStore(db)
