"""CRUD operations for artifacts, notes, chunks, links, and reviews."""

import uuid
from datetime import datetime

from sqlalchemy import select, update, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Artifact, Classification, Chunk, Note, Link, ReviewQueue


# ── Artifacts ───────────────────────────────────────────────────

async def create_artifact(session: AsyncSession, **kwargs) -> Artifact:
    artifact = Artifact(**kwargs)
    session.add(artifact)
    await session.commit()
    await session.refresh(artifact)
    return artifact


async def get_artifact(session: AsyncSession, artifact_id: uuid.UUID) -> Artifact | None:
    return await session.get(Artifact, artifact_id)


async def get_artifact_by_discord_id(session: AsyncSession, message_id: str) -> Artifact | None:
    result = await session.execute(select(Artifact).where(Artifact.discord_message_id == message_id))
    return result.scalar_one_or_none()


# ── Classifications ─────────────────────────────────────────────

async def create_classification(session: AsyncSession, **kwargs) -> Classification:
    classification = Classification(**kwargs)
    session.add(classification)
    await session.commit()
    await session.refresh(classification)
    return classification


async def get_final_classification(session: AsyncSession, artifact_id: uuid.UUID) -> Classification | None:
    result = await session.execute(
        select(Classification)
        .where(Classification.artifact_id == artifact_id, Classification.is_final == True)
        .order_by(Classification.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Notes ───────────────────────────────────────────────────────

async def create_note(session: AsyncSession, **kwargs) -> Note:
    note = Note(**kwargs)
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return note


async def get_note(session: AsyncSession, note_id: uuid.UUID) -> Note | None:
    return await session.get(Note, note_id)


async def find_notes_by_title(session: AsyncSession, title: str, category: str | None = None) -> list[Note]:
    query = select(Note).where(func.lower(Note.title).contains(title.lower()))
    if category:
        query = query.where(Note.category == category)
    result = await session.execute(query.limit(10))
    return list(result.scalars().all())


async def list_notes(
    session: AsyncSession,
    category: str | None = None,
    status: str = "active",
    limit: int = 25,
) -> list[Note]:
    query = select(Note).where(Note.status == status)
    if category:
        query = query.where(Note.category == category)
    query = query.order_by(Note.updated_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def update_note(session: AsyncSession, note_id: uuid.UUID, **kwargs) -> Note | None:
    kwargs["updated_at"] = datetime.utcnow()
    await session.execute(update(Note).where(Note.id == note_id).values(**kwargs))
    await session.commit()
    return await get_note(session, note_id)


# ── Chunks ──────────────────────────────────────────────────────

async def create_chunks(session: AsyncSession, chunks: list[dict]) -> list[Chunk]:
    chunk_objects = [Chunk(**c) for c in chunks]
    session.add_all(chunk_objects)
    await session.commit()
    for c in chunk_objects:
        await session.refresh(c)
    return chunk_objects


async def vector_search(
    session: AsyncSession,
    query_embedding: list[float],
    limit: int = 20,
    min_similarity: float = 0.3,
    category: str | None = None,
) -> list[dict]:
    """Cosine similarity search on chunks. Returns dicts with chunk + similarity."""
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    sql = text("""
        SELECT c.id, c.content, c.artifact_id, c.note_id, c.chunk_index, c.metadata,
               1 - (c.embedding <=> :embedding::vector) AS similarity
        FROM chunks c
        WHERE c.embedding IS NOT NULL
          AND 1 - (c.embedding <=> :embedding::vector) > :min_similarity
        ORDER BY c.embedding <=> :embedding::vector
        LIMIT :limit
    """)

    result = await session.execute(
        sql, {"embedding": embedding_str, "min_similarity": min_similarity, "limit": limit}
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]


# ── Links ───────────────────────────────────────────────────────

async def create_link(session: AsyncSession, **kwargs) -> Link:
    link = Link(**kwargs)
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return link


async def get_related(session: AsyncSession, source_type: str, source_id: uuid.UUID) -> list[Link]:
    result = await session.execute(
        select(Link).where(Link.source_type == source_type, Link.source_id == source_id)
    )
    return list(result.scalars().all())


# ── Review Queue ────────────────────────────────────────────────

async def create_review(session: AsyncSession, **kwargs) -> ReviewQueue:
    review = ReviewQueue(**kwargs)
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return review


async def get_review_by_thread(session: AsyncSession, thread_id: str) -> ReviewQueue | None:
    result = await session.execute(
        select(ReviewQueue).where(ReviewQueue.discord_thread_id == thread_id, ReviewQueue.status == "pending")
    )
    return result.scalar_one_or_none()


async def get_pending_reviews(session: AsyncSession) -> list[ReviewQueue]:
    result = await session.execute(
        select(ReviewQueue).where(ReviewQueue.status == "pending").order_by(ReviewQueue.created_at)
    )
    return list(result.scalars().all())


async def resolve_review(session: AsyncSession, review_id: uuid.UUID, answer: str) -> ReviewQueue:
    await session.execute(
        update(ReviewQueue)
        .where(ReviewQueue.id == review_id)
        .values(answer=answer, status="answered", answered_at=datetime.utcnow())
    )
    await session.commit()
    result = await session.execute(select(ReviewQueue).where(ReviewQueue.id == review_id))
    return result.scalar_one()
