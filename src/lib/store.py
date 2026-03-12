"""CRUD operations and story helpers for the brain."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import normalize_category, normalize_tags
from src.models import (
    Artifact,
    Classification,
    Chunk,
    Digest,
    JournalEntry,
    Link,
    Note,
    OAuthCredential,
    ProjectRepo,
    ReviewQueue,
    SourceItem,
    SyncRun,
    SyncSource,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_artifact(session: AsyncSession, **kwargs) -> Artifact:
    kwargs.setdefault("source", "manual")
    kwargs.setdefault("created_at", _utcnow())
    kwargs.setdefault("updated_at", _utcnow())
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


async def update_artifact(session: AsyncSession, artifact_id: uuid.UUID, **kwargs) -> Artifact | None:
    kwargs.setdefault("updated_at", _utcnow())
    await session.execute(update(Artifact).where(Artifact.id == artifact_id).values(**kwargs))
    await session.commit()
    return await get_artifact(session, artifact_id)


async def create_classification(session: AsyncSession, **kwargs) -> Classification:
    kwargs["category"] = normalize_category(kwargs.get("category"))
    kwargs["tags"] = normalize_tags(kwargs.get("tags"))
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


async def create_note(session: AsyncSession, **kwargs) -> Note:
    kwargs["category"] = normalize_category(kwargs.get("category"))
    kwargs["tags"] = normalize_tags(kwargs.get("tags"))
    kwargs.setdefault("created_at", _utcnow())
    kwargs.setdefault("updated_at", _utcnow())
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
        query = query.where(Note.category == normalize_category(category))
    result = await session.execute(query.limit(10))
    return list(result.scalars().all())


async def get_or_create_project_note(
    session: AsyncSession,
    title: str,
    *,
    content: str | None = None,
    tags: list[str] | None = None,
) -> Note:
    matches = await find_notes_by_title(session, title, "project")
    if matches:
        return matches[0]

    return await create_note(
        session,
        category="project",
        title=title,
        content=content,
        tags=tags or [],
        priority="medium",
    )


async def list_notes(
    session: AsyncSession,
    category: str | None = None,
    status: str = "active",
    limit: int = 25,
) -> list[Note]:
    query = select(Note).where(Note.status == status)
    if category:
        query = query.where(Note.category == normalize_category(category))
    query = query.order_by(Note.updated_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def list_active_project_aliases(session: AsyncSession, limit: int = 25) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()

    projects = await list_notes(session, category="project", limit=limit)
    for project in projects:
        cleaned = (project.title or "").strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            aliases.append(cleaned)

    repo_result = await session.execute(
        select(ProjectRepo).order_by(ProjectRepo.updated_at.desc(), ProjectRepo.created_at.desc()).limit(limit * 2)
    )
    for repo in repo_result.scalars():
        for candidate in (repo.repo_name, repo.local_path):
            cleaned = (candidate or "").strip()
            if not cleaned:
                continue
            leaf = cleaned.rstrip("/").split("/")[-1]
            for value in (cleaned, leaf):
                key = value.lower()
                if value and key not in seen:
                    seen.add(key)
                    aliases.append(value)

    return aliases[: limit * 2]


async def update_note(session: AsyncSession, note_id: uuid.UUID, **kwargs) -> Note | None:
    if "category" in kwargs:
        kwargs["category"] = normalize_category(kwargs["category"])
    if "tags" in kwargs:
        kwargs["tags"] = normalize_tags(kwargs["tags"])
    kwargs["updated_at"] = _utcnow()
    await session.execute(update(Note).where(Note.id == note_id).values(**kwargs))
    await session.commit()
    return await get_note(session, note_id)


async def create_chunks(session: AsyncSession, chunks: list[dict]) -> list[Chunk]:
    chunk_objects = [Chunk(**c) for c in chunks]
    session.add_all(chunk_objects)
    await session.commit()
    for chunk in chunk_objects:
        await session.refresh(chunk)
    return chunk_objects


async def reset_artifact_processing(session: AsyncSession, artifact_id: uuid.UUID) -> None:
    artifact = await get_artifact(session, artifact_id)
    if artifact:
        metadata = dict(artifact.metadata_ or {})
        for key in (
            "discord_receipt_message_id",
            "discord_planner_card_channel_id",
            "discord_planner_card_message_id",
            "discord_weekly_rollup_channel_id",
            "discord_weekly_rollup_message_id",
        ):
            metadata.pop(key, None)
        artifact.metadata_ = metadata
        artifact.summary = None
        artifact.updated_at = _utcnow()

    await session.execute(delete(ReviewQueue).where(ReviewQueue.artifact_id == artifact_id))
    await session.execute(delete(JournalEntry).where(JournalEntry.artifact_id == artifact_id))
    await session.execute(delete(Chunk).where(Chunk.artifact_id == artifact_id))
    await session.execute(delete(Classification).where(Classification.artifact_id == artifact_id))
    await session.commit()


async def vector_search(
    session: AsyncSession,
    query_embedding: list[float],
    limit: int = 20,
    min_similarity: float = 0.3,
    category: str | None = None,
) -> list[dict]:
    """Cosine similarity search on chunks with note/classification category filtering."""
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    normalized_category = normalize_category(category) if category else None

    sql = text(
        """
        SELECT
            c.id,
            c.content,
            c.artifact_id,
            c.note_id,
            c.chunk_index,
            c.metadata,
            COALESCE(n.created_at, a.created_at) AS created_at,
            COALESCE(n.category, cls.category) AS resolved_category,
            1 - (c.embedding <=> CAST(:embedding AS vector)) AS similarity
        FROM chunks c
        LEFT JOIN notes n ON c.note_id = n.id
        LEFT JOIN artifacts a ON c.artifact_id = a.id
        LEFT JOIN classifications cls
            ON cls.artifact_id = c.artifact_id
           AND cls.is_final = TRUE
        WHERE c.embedding IS NOT NULL
          AND 1 - (c.embedding <=> CAST(:embedding AS vector)) > :min_similarity
          AND (:category IS NULL OR COALESCE(n.category, cls.category) = :category)
        ORDER BY c.embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
        """
    )

    result = await session.execute(
        sql,
        {
            "embedding": embedding_str,
            "min_similarity": min_similarity,
            "limit": limit,
            "category": normalized_category,
        },
    )
    return [dict(row) for row in result.mappings().all()]


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


async def create_review(session: AsyncSession, **kwargs) -> ReviewQueue:
    review = ReviewQueue(**kwargs)
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return review


async def get_review_by_thread(session: AsyncSession, thread_id: str) -> ReviewQueue | None:
    result = await session.execute(
        select(ReviewQueue).where(
            ReviewQueue.discord_thread_id == thread_id,
            ReviewQueue.status == "pending",
        )
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
        .values(answer=answer, status="answered", answered_at=_utcnow())
    )
    await session.commit()
    result = await session.execute(select(ReviewQueue).where(ReviewQueue.id == review_id))
    return result.scalar_one()


async def set_review_thread(session: AsyncSession, review_id: uuid.UUID, thread_id: str) -> ReviewQueue | None:
    await session.execute(
        update(ReviewQueue)
        .where(ReviewQueue.id == review_id)
        .values(discord_thread_id=thread_id)
    )
    await session.commit()
    result = await session.execute(select(ReviewQueue).where(ReviewQueue.id == review_id))
    return result.scalar_one_or_none()


async def create_digest(session: AsyncSession, *, digest_date: date, payload: dict) -> Digest:
    digest = Digest(digest_date=digest_date, payload=payload)
    session.add(digest)
    await session.commit()
    await session.refresh(digest)
    return digest


async def get_digest_by_date(session: AsyncSession, digest_date: date) -> Digest | None:
    result = await session.execute(select(Digest).where(Digest.digest_date == digest_date))
    return result.scalar_one_or_none()


async def create_journal_entry(session: AsyncSession, **kwargs) -> JournalEntry:
    kwargs["tags"] = normalize_tags(kwargs.get("tags"))
    kwargs.setdefault("source_links", [])
    kwargs.setdefault("happened_at", _utcnow())
    kwargs.setdefault("created_at", _utcnow())
    journal_entry = JournalEntry(**kwargs)
    session.add(journal_entry)
    await session.commit()
    await session.refresh(journal_entry)
    return journal_entry


async def list_recent_activity(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID | None = None,
    limit: int = 25,
) -> list[JournalEntry]:
    query = select(JournalEntry)
    if project_note_id:
        query = query.where(JournalEntry.project_note_id == project_note_id)
    query = query.order_by(JournalEntry.happened_at.desc(), JournalEntry.created_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_project_story(session: AsyncSession, project_note_id: uuid.UUID) -> dict | None:
    project = await get_note(session, project_note_id)
    if not project:
        return None

    journal_entries = await list_recent_activity(session, project_note_id=project_note_id, limit=50)
    repos_result = await session.execute(
        select(ProjectRepo).where(ProjectRepo.project_note_id == project_note_id).order_by(ProjectRepo.is_primary.desc())
    )
    source_items_result = await session.execute(
        select(SourceItem)
        .where(SourceItem.project_note_id == project_note_id)
        .order_by(SourceItem.happened_at.desc().nullslast(), SourceItem.created_at.desc())
        .limit(25)
    )
    related_links = await get_related(session, "note", project_note_id)

    return {
        "project": project,
        "journal_entries": list(journal_entries),
        "repos": list(repos_result.scalars().all()),
        "source_items": list(source_items_result.scalars().all()),
        "related_links": related_links,
    }


async def upsert_sync_source(
    session: AsyncSession,
    *,
    source_type: str,
    name: str,
    status: str = "active",
    config: dict | None = None,
) -> SyncSource:
    result = await session.execute(
        select(SyncSource).where(SyncSource.source_type == source_type, SyncSource.name == name)
    )
    source = result.scalar_one_or_none()
    if source:
        source.status = status
        source.config = config or source.config
        source.updated_at = _utcnow()
        await session.commit()
        await session.refresh(source)
        return source

    source = SyncSource(source_type=source_type, name=name, status=status, config=config or {})
    session.add(source)
    await session.commit()
    await session.refresh(source)
    return source


async def start_sync_run(
    session: AsyncSession,
    *,
    sync_source_id: uuid.UUID,
    mode: str,
    metadata_: dict | None = None,
) -> SyncRun:
    sync_run = SyncRun(
        sync_source_id=sync_source_id,
        mode=mode,
        status="running",
        started_at=_utcnow(),
        metadata_=metadata_ or {},
    )
    session.add(sync_run)
    await session.commit()
    await session.refresh(sync_run)
    return sync_run


async def finish_sync_run(
    session: AsyncSession,
    sync_run_id: uuid.UUID,
    *,
    status: str,
    items_seen: int,
    items_imported: int,
    error: str | None = None,
) -> SyncRun | None:
    await session.execute(
        update(SyncRun)
        .where(SyncRun.id == sync_run_id)
        .values(
            status=status,
            items_seen=items_seen,
            items_imported=items_imported,
            error=error,
            finished_at=_utcnow(),
        )
    )
    await session.commit()
    result = await session.execute(select(SyncRun).where(SyncRun.id == sync_run_id))
    return result.scalar_one_or_none()


async def touch_sync_source(session: AsyncSession, sync_source_id: uuid.UUID) -> None:
    await session.execute(
        update(SyncSource)
        .where(SyncSource.id == sync_source_id)
        .values(last_synced_at=_utcnow(), updated_at=_utcnow())
    )
    await session.commit()


async def create_source_item(session: AsyncSession, **kwargs) -> SourceItem:
    source_item = SourceItem(**kwargs)
    session.add(source_item)
    await session.commit()
    await session.refresh(source_item)
    return source_item


async def upsert_source_item(
    session: AsyncSession,
    *,
    sync_source_id: uuid.UUID,
    external_id: str,
    title: str,
    summary: str | None = None,
    payload: dict | None = None,
    content_hash: str | None = None,
    external_url: str | None = None,
    project_note_id: uuid.UUID | None = None,
    artifact_id: uuid.UUID | None = None,
    happened_at: datetime | None = None,
) -> tuple[SourceItem, bool]:
    result = await session.execute(
        select(SourceItem).where(
            SourceItem.sync_source_id == sync_source_id,
            SourceItem.external_id == external_id,
        )
    )
    source_item = result.scalar_one_or_none()
    if source_item:
        source_item.title = title
        source_item.summary = summary
        source_item.payload = payload or {}
        source_item.content_hash = content_hash
        source_item.external_url = external_url
        source_item.project_note_id = project_note_id
        source_item.artifact_id = artifact_id
        source_item.happened_at = happened_at
        await session.commit()
        await session.refresh(source_item)
        return source_item, False

    source_item = SourceItem(
        sync_source_id=sync_source_id,
        external_id=external_id,
        title=title,
        summary=summary,
        payload=payload or {},
        content_hash=content_hash,
        external_url=external_url,
        project_note_id=project_note_id,
        artifact_id=artifact_id,
        happened_at=happened_at,
    )
    session.add(source_item)
    await session.commit()
    await session.refresh(source_item)
    return source_item, True


async def upsert_project_repo(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID,
    repo_name: str,
    repo_owner: str | None = None,
    repo_url: str | None = None,
    branch: str | None = None,
    local_path: str | None = None,
    is_primary: bool = False,
) -> ProjectRepo:
    result = await session.execute(
        select(ProjectRepo).where(
            ProjectRepo.project_note_id == project_note_id,
            ProjectRepo.repo_name == repo_name,
        )
    )
    repo = result.scalar_one_or_none()
    if repo:
        repo.repo_owner = repo_owner
        repo.repo_url = repo_url
        repo.branch = branch
        repo.local_path = local_path
        repo.is_primary = is_primary
        repo.updated_at = _utcnow()
        await session.commit()
        await session.refresh(repo)
        return repo

    repo = ProjectRepo(
        project_note_id=project_note_id,
        repo_name=repo_name,
        repo_owner=repo_owner,
        repo_url=repo_url,
        branch=branch,
        local_path=local_path,
        is_primary=is_primary,
    )
    session.add(repo)
    await session.commit()
    await session.refresh(repo)
    return repo


async def get_project_repo_mappings(session: AsyncSession, project_note_id: uuid.UUID) -> list[ProjectRepo]:
    result = await session.execute(select(ProjectRepo).where(ProjectRepo.project_note_id == project_note_id))
    return list(result.scalars().all())


async def get_oauth_credentials(session: AsyncSession, provider: str) -> list[OAuthCredential]:
    result = await session.execute(select(OAuthCredential).where(OAuthCredential.provider == provider))
    return list(result.scalars().all())
