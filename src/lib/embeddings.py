"""Embeddings wrapper — NVIDIA NIM (free-tier).

We call NIM's embeddings endpoint directly via httpx rather than through the
OpenAI SDK. NIM accepts the OpenAI request shape plus an `input_type` field
that the retrieval-tuned models (e.g. `nvidia/nv-embedqa-e5-v5`) require
("passage" when indexing chunks, "query" when embedding a search input).
"""

from __future__ import annotations

from functools import lru_cache

import httpx

from src.config import settings


@lru_cache(maxsize=1)
def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.nvidia_base_url,
        headers={
            "Authorization": f"Bearer {settings.nvidia_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=60.0,
    )


async def _embed(inputs: list[str], *, input_type: str) -> list[list[float]]:
    cleaned = [t or " " for t in inputs]
    response = await _http_client().post(
        "embeddings",
        json={
            "model": settings.embedding_model,
            "input": cleaned,
            "input_type": input_type,
        },
    )
    response.raise_for_status()
    data = response.json()
    items = sorted(data.get("data") or [], key=lambda item: item.get("index", 0))
    return [list(item["embedding"]) for item in items]


async def embed_text(text: str) -> list[float]:
    """Embed a single query string."""
    vectors = await _embed([text], input_type="query")
    return vectors[0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of document chunks (passages) for indexing."""
    if not texts:
        return []
    return await _embed(texts, input_type="passage")
