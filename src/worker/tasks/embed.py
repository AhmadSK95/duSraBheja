"""Embed task — chunk text and generate embeddings."""

import logging
import uuid

from src.config import settings
from src.database import async_session
from src.lib.embeddings import embed_batch
from src.lib.store import get_artifact, create_chunks

log = logging.getLogger("brain-worker.embed")


def _chunk_text(text: str, max_tokens: int = 512, overlap_tokens: int = 64) -> list[str]:
    """Split text into chunks on paragraph boundaries.

    Approximation: 1 token ~= 4 chars.
    """
    max_chars = max_tokens * 4
    overlap_chars = overlap_tokens * 4
    min_chunk_chars = 128  # ~32 tokens minimum

    # Split on double newlines (paragraphs)
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current_chunk) + len(para) + 2 <= max_chars:
            current_chunk = current_chunk + "\n\n" + para if current_chunk else para
        else:
            if current_chunk and len(current_chunk) >= min_chunk_chars:
                chunks.append(current_chunk)
            # Start new chunk with overlap
            if chunks and overlap_chars > 0:
                prev = chunks[-1]
                overlap_text = prev[-overlap_chars:]
                current_chunk = overlap_text + "\n\n" + para
            else:
                current_chunk = para

            # If single paragraph is too long, split on sentences
            if len(current_chunk) > max_chars:
                sentences = current_chunk.replace(". ", ".\n").split("\n")
                current_chunk = ""
                for sent in sentences:
                    if len(current_chunk) + len(sent) + 1 <= max_chars:
                        current_chunk = current_chunk + " " + sent if current_chunk else sent
                    else:
                        if current_chunk and len(current_chunk) >= min_chunk_chars:
                            chunks.append(current_chunk)
                        current_chunk = sent

    if current_chunk and len(current_chunk) >= min_chunk_chars:
        chunks.append(current_chunk)

    return chunks


async def generate_embeddings(ctx, artifact_id: str):
    """Chunk artifact text and generate embeddings."""
    artifact_uuid = uuid.UUID(artifact_id)

    async with async_session() as session:
        artifact = await get_artifact(session, artifact_uuid)
        if not artifact or not artifact.raw_text:
            log.warning(f"Artifact {artifact_id} has no text for embedding")
            return

        # Chunk the text
        chunks = _chunk_text(
            artifact.raw_text,
            max_tokens=settings.chunk_max_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        )

        if not chunks:
            log.warning(f"No chunks generated for artifact {artifact_id}")
            return

        log.info(f"Generated {len(chunks)} chunks for artifact {artifact_id}")

        # Generate embeddings in batches
        batch_size = 20
        all_embeddings = []
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            embeddings = await embed_batch(batch)
            all_embeddings.extend(embeddings)

        # Store chunks with embeddings
        chunk_records = [
            {
                "artifact_id": artifact_uuid,
                "chunk_index": i,
                "content": chunk_text,
                "token_count": len(chunk_text) // 4,  # Approximate
                "embedding": embedding,
            }
            for i, (chunk_text, embedding) in enumerate(zip(chunks, all_embeddings))
        ]

        await create_chunks(session, chunk_records)
        log.info(f"Stored {len(chunk_records)} chunks with embeddings for artifact {artifact_id}")
