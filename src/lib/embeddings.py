"""OpenAI embeddings wrapper for text-embedding-3-small."""

from src.config import settings
from src.lib.provider_clients import openai_client_for_role
from src.services.providers import model_for_role


async def embed_text(text: str) -> list[float]:
    """Embed a single text string. Returns 1536-dim vector."""
    client = openai_client_for_role("embed")
    response = await client.embeddings.create(
        model=model_for_role("embed"),
        input=text,
        dimensions=settings.embedding_dimensions,
    )
    return response.data[0].embedding


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in one API call. Max ~8000 tokens per batch."""
    if not texts:
        return []
    client = openai_client_for_role("embed")
    response = await client.embeddings.create(
        model=model_for_role("embed"),
        input=texts,
        dimensions=settings.embedding_dimensions,
    )
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
