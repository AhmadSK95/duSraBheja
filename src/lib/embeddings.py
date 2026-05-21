"""Embeddings wrapper — NVIDIA NIM (free-tier, OpenAI-compatible).

NIM's retrieval-tuned models (e.g. `nvidia/nv-embedqa-e5-v5`) are asymmetric
and require an `input_type` field: "passage" when indexing a document chunk,
"query" when embedding a search input. We pass that via the OpenAI SDK's
`extra_body` parameter.
"""

from __future__ import annotations

from src.config import settings
from src.lib.llm import _client


async def _embed(inputs: list[str], *, input_type: str) -> list[list[float]]:
    cleaned = [t or " " for t in inputs]
    response = await _client().embeddings.create(
        model=settings.embedding_model,
        input=cleaned,
        extra_body={"input_type": input_type},
    )
    ordered = sorted(response.data, key=lambda item: item.index)
    return [list(item.embedding) for item in ordered]


async def embed_text(text: str) -> list[float]:
    """Embed a single query string."""
    vectors = await _embed([text], input_type="query")
    return vectors[0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of document chunks (passages) for indexing."""
    if not texts:
        return []
    return await _embed(texts, input_type="passage")
