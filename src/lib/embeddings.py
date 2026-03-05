"""OpenAI embeddings wrapper for text-embedding-3-small."""

import openai

from src.config import settings

client = openai.AsyncOpenAI(api_key=settings.openai_api_key)


async def embed_text(text: str) -> list[float]:
    """Embed a single text string. Returns 1536-dim vector."""
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=text,
        dimensions=settings.embedding_dimensions,
    )
    return response.data[0].embedding


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in one API call. Max ~8000 tokens per batch."""
    if not texts:
        return []
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=texts,
        dimensions=settings.embedding_dimensions,
    )
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
