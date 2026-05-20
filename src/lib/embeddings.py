"""Embeddings wrapper — NVIDIA NIM (free-tier, OpenAI-compatible).

Uses the same NIM async client as src.lib.llm. Default model is
`nvidia/nv-embedqa-e5-v5` (1024-dim, retrieval-tuned).
"""

from __future__ import annotations

from src.config import settings
from src.lib.llm import _client


async def embed_text(text: str) -> list[float]:
    """Embed a single text string. Returns a `settings.embedding_dimensions`-dim vector."""
    response = await _client().embeddings.create(
        model=settings.embedding_model,
        input=text or " ",
    )
    return list(response.data[0].embedding)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in one API call."""
    if not texts:
        return []
    cleaned = [t or " " for t in texts]
    response = await _client().embeddings.create(
        model=settings.embedding_model,
        input=cleaned,
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    return [list(item.embedding) for item in ordered]
