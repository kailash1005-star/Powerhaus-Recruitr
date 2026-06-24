"""
Embedding Service — OpenAI text embeddings.

Used to embed (a) the Docling markdown of each CV for retrieval, and (b) the
parsed JD. Batched and retried. Returns vectors plus the model/dim so callers
can store embedding provenance on each record (auditability).

The OpenAI client is synchronous; calls are offloaded to a thread so they never
block the FastAPI event loop.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List

from app.config import settings

logger = logging.getLogger(__name__)

# OpenAI allows large batches; keep a safe cap.
_MAX_BATCH = 256
_EMBED_VERSION = "v1"

_client = None


def _get_client():
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set — required for embeddings.")
        from openai import OpenAI
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def embedding_version() -> str:
    return f"{settings.EMBEDDING_MODEL}:{_EMBED_VERSION}"


def _embed_sync(texts: List[str]) -> List[List[float]]:
    client = _get_client()
    model = settings.EMBEDDING_MODEL
    out: List[List[float]] = []

    for i in range(0, len(texts), _MAX_BATCH):
        batch = texts[i:i + _MAX_BATCH]
        # OpenAI rejects empty strings — substitute a single space.
        safe = [t if (t and t.strip()) else " " for t in batch]
        last_err = None
        for attempt in range(1, 4):
            try:
                resp = client.embeddings.create(model=model, input=safe)
                out.extend([d.embedding for d in resp.data])
                last_err = None
                break
            except Exception as e:  # noqa: BLE001 - retry any transient API error
                last_err = e
                logger.warning("[Embedding] batch %d attempt %d failed: %s", i // _MAX_BATCH, attempt, e)
                time.sleep(min(2 ** attempt, 8))
        if last_err is not None:
            raise last_err
    return out


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a list of texts → list of vectors (same order). Empty input → []."""
    if not texts:
        return []
    return await asyncio.to_thread(_embed_sync, list(texts))


async def embed_text(text: str) -> List[float]:
    """Embed a single text → one vector."""
    vecs = await embed_texts([text])
    return vecs[0] if vecs else []
