"""Shared artifact indexing helpers for chunking and embeddings."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.lib.embeddings import embed_batch
from src.lib.store import create_chunks, get_artifact
from src.worker.tasks.embed import _chunk_text


async def index_artifact(session: AsyncSession, artifact_id: uuid.UUID) -> int:
    artifact = await get_artifact(session, artifact_id)
    if not artifact or not artifact.raw_text:
        return 0

    chunks = _chunk_text(
        artifact.raw_text,
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    if not chunks:
        return 0

    embeddings = await embed_batch(chunks)
    chunk_records = [
        {
            "artifact_id": artifact_id,
            "chunk_index": index,
            "content": chunk_text,
            "token_count": len(chunk_text) // 4,
            "embedding": embedding,
        }
        for index, (chunk_text, embedding) in enumerate(zip(chunks, embeddings))
    ]
    await create_chunks(session, chunk_records)
    return len(chunk_records)
