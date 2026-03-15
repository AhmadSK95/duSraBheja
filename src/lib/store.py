"""CRUD operations and story helpers for the brain."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import normalize_category, normalize_tags
from src.models import (
    Artifact,
    Board,
    Classification,
    Chunk,
    ConversationSession,
    DigestPreference,
    Digest,
    EvalCaseResult,
    EvalRun,
    JournalEntry,
    Link,
    Note,
    OAuthCredential,
    ProjectRepo,
    ProjectAlias,
    ProjectStateSnapshot,
    ProtectedContent,
    ReviewQueue,
    Reminder,
    RetrievalTrace,
    SourceItem,
    StoryConnection,
    SyncRun,
    SyncSource,
    VoiceProfile,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_alias(alias: str | None) -> str:
    cleaned = (alias or "").strip().lower()
    return "".join(ch if ch.isalnum() else "-" for ch in cleaned).strip("-")


def _contains_any(columns: list, phrases: list[str]):
    cleaned_phrases = [phrase.strip().lower() for phrase in phrases if (phrase or "").strip()]
    if not cleaned_phrases:
        return None
    predicates = [
        func.lower(func.coalesce(column, "")).contains(phrase)
        for phrase in cleaned_phrases
        for column in columns
    ]
    return or_(*predicates)


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
    kwargs.setdefault("capture_intent", "thought")
    kwargs.setdefault("intent_confidence", 0.5)
    kwargs.setdefault("validation_status", "validated")
    kwargs.setdefault("quality_issues", [])
    kwargs.setdefault("eligible_for_boards", True)
    kwargs.setdefault("eligible_for_project_state", True)
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


async def get_classification(session: AsyncSession, classification_id: uuid.UUID) -> Classification | None:
    return await session.get(Classification, classification_id)


async def get_latest_classification(session: AsyncSession, artifact_id: uuid.UUID) -> Classification | None:
    result = await session.execute(
        select(Classification)
        .where(Classification.artifact_id == artifact_id)
        .order_by(Classification.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def update_classification(session: AsyncSession, classification_id: uuid.UUID, **kwargs) -> Classification | None:
    await session.execute(update(Classification).where(Classification.id == classification_id).values(**kwargs))
    await session.commit()
    return await get_classification(session, classification_id)


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
    exact = await session.execute(
        select(Note).where(
            Note.category == "project",
            func.lower(Note.title) == title.strip().lower(),
        )
    )
    existing = exact.scalar_one_or_none()
    if existing:
        return existing

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


async def list_project_notes(session: AsyncSession, *, limit: int = 200) -> list[Note]:
    result = await session.execute(
        select(Note)
        .where(Note.category == "project")
        .order_by(Note.updated_at.desc(), Note.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_recent_planner_notes(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    limit: int = 50,
) -> list[Note]:
    query = select(Note).where(Note.category.in_(("daily_planner", "weekly_planner")))
    if since:
        query = query.where(Note.updated_at >= since)
    query = query.order_by(Note.updated_at.desc(), Note.created_at.desc()).limit(limit)
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

    alias_result = await session.execute(
        select(ProjectAlias).order_by(ProjectAlias.updated_at.desc(), ProjectAlias.created_at.desc()).limit(limit * 3)
    )
    for alias in alias_result.scalars():
        cleaned = (alias.alias or "").strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            aliases.append(cleaned)

    return aliases[: limit * 2]


async def upsert_project_alias(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID,
    alias: str,
    source_type: str | None = None,
    source_ref: str | None = None,
    confidence: float = 0.8,
    is_manual: bool = False,
    metadata_: dict | None = None,
) -> ProjectAlias:
    normalized_alias = _normalize_alias(alias)
    result = await session.execute(
        select(ProjectAlias).where(ProjectAlias.normalized_alias == normalized_alias)
    )
    project_alias = result.scalar_one_or_none()
    if project_alias:
        project_alias.project_note_id = project_note_id
        project_alias.alias = alias
        project_alias.source_type = source_type
        project_alias.source_ref = source_ref
        project_alias.confidence = confidence
        project_alias.is_manual = is_manual
        project_alias.metadata_ = metadata_ or {}
        project_alias.updated_at = _utcnow()
        await session.commit()
        await session.refresh(project_alias)
        return project_alias

    project_alias = ProjectAlias(
        project_note_id=project_note_id,
        alias=alias,
        normalized_alias=normalized_alias,
        source_type=source_type,
        source_ref=source_ref,
        confidence=confidence,
        is_manual=is_manual,
        metadata_=metadata_ or {},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(project_alias)
    await session.commit()
    await session.refresh(project_alias)
    return project_alias


async def resolve_project_alias(session: AsyncSession, alias: str) -> ProjectAlias | None:
    normalized_alias = _normalize_alias(alias)
    if not normalized_alias:
        return None
    result = await session.execute(
        select(ProjectAlias).where(ProjectAlias.normalized_alias == normalized_alias)
    )
    return result.scalar_one_or_none()


async def list_project_aliases(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[ProjectAlias]:
    query = select(ProjectAlias)
    if project_note_id:
        query = query.where(ProjectAlias.project_note_id == project_note_id)
    query = query.order_by(ProjectAlias.is_manual.desc(), ProjectAlias.updated_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


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
    category_filter = ""
    params = {
        "embedding": embedding_str,
        "min_similarity": min_similarity,
        "limit": limit,
    }
    if normalized_category is not None:
        category_filter = "AND COALESCE(n.category, cls.category) = :category"
        params["category"] = normalized_category

    sql = text(
        f"""
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
          {category_filter}
        ORDER BY c.embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
        """
    )

    result = await session.execute(sql, params)
    return [dict(row) for row in result.mappings().all()]


async def search_artifacts_text(
    session: AsyncSession,
    phrases: list[str],
    *,
    validation_status: str = "validated",
    limit: int = 12,
) -> list[dict]:
    predicate = _contains_any([Artifact.summary, Artifact.raw_text], phrases)
    if predicate is None:
        return []

    final_class = select(Classification).where(Classification.is_final == True).subquery()
    query = (
        select(
            Artifact,
            final_class.c.category,
            final_class.c.capture_intent,
            final_class.c.validation_status,
            final_class.c.tags,
        )
        .join(final_class, final_class.c.artifact_id == Artifact.id)
        .where(predicate)
        .order_by(Artifact.created_at.desc())
        .limit(limit)
    )
    if validation_status:
        query = query.where(final_class.c.validation_status == validation_status)

    result = await session.execute(query)
    rows: list[dict] = []
    cleaned_phrases = [phrase.strip().lower() for phrase in phrases if phrase.strip()]
    for row in result.all():
        artifact = row[0]
        haystacks = " ".join(
            part for part in ((artifact.summary or "").lower(), (artifact.raw_text or "").lower()) if part
        )
        matched = [phrase for phrase in cleaned_phrases if phrase in haystacks]
        rows.append(
            {
                "artifact": artifact,
                "category": row.category,
                "capture_intent": row.capture_intent,
                "validation_status": row.validation_status,
                "tags": row.tags or [],
                "matched_phrases": matched,
            }
        )
    return rows


async def search_notes_text(
    session: AsyncSession,
    phrases: list[str],
    *,
    limit: int = 10,
) -> list[dict]:
    predicate = _contains_any([Note.title, Note.content], phrases)
    if predicate is None:
        return []
    result = await session.execute(
        select(Note)
        .where(predicate)
        .order_by(Note.updated_at.desc(), Note.created_at.desc())
        .limit(limit)
    )
    cleaned_phrases = [phrase.strip().lower() for phrase in phrases if phrase.strip()]
    rows: list[dict] = []
    for note in result.scalars().all():
        haystacks = " ".join(part for part in ((note.title or "").lower(), (note.content or "").lower()) if part)
        matched = [phrase for phrase in cleaned_phrases if phrase in haystacks]
        rows.append({"note": note, "matched_phrases": matched})
    return rows


async def search_source_items_text(
    session: AsyncSession,
    phrases: list[str],
    *,
    limit: int = 10,
) -> list[SourceItem]:
    predicate = _contains_any([SourceItem.title, SourceItem.summary, SourceItem.external_url], phrases)
    if predicate is None:
        return []
    result = await session.execute(
        select(SourceItem)
        .where(predicate)
        .order_by(SourceItem.happened_at.desc().nullslast(), SourceItem.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


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
    kwargs.setdefault("review_kind", "moderation")
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


async def get_review(session: AsyncSession, review_id: uuid.UUID) -> ReviewQueue | None:
    return await session.get(ReviewQueue, review_id)


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


async def moderate_review(
    session: AsyncSession,
    review_id: uuid.UUID,
    *,
    status: str,
    resolution: str | None = None,
    moderation_notes: str | None = None,
    resolved_by: str | None = None,
) -> ReviewQueue | None:
    values = {
        "status": status,
        "resolution": resolution,
        "moderation_notes": moderation_notes,
        "resolved_by": resolved_by,
    }
    if status in {"approved", "rejected", "resolved"}:
        values["resolved_at"] = _utcnow()
    await session.execute(update(ReviewQueue).where(ReviewQueue.id == review_id).values(**values))
    await session.commit()
    return await get_review(session, review_id)


async def create_digest(session: AsyncSession, *, digest_date: date, payload: dict) -> Digest:
    digest = Digest(digest_date=digest_date, payload=payload)
    session.add(digest)
    await session.commit()
    await session.refresh(digest)
    return digest


async def get_digest_by_date(session: AsyncSession, digest_date: date) -> Digest | None:
    result = await session.execute(select(Digest).where(Digest.digest_date == digest_date))
    return result.scalar_one_or_none()


async def upsert_board(
    session: AsyncSession,
    *,
    board_type: str,
    generated_for_date: date,
    coverage_start: datetime,
    coverage_end: datetime,
    payload: dict,
    source_artifact_ids: list[str] | None = None,
    excluded_artifact_ids: list[str] | None = None,
    status: str = "ready",
    discord_channel_name: str | None = None,
    discord_message_id: str | None = None,
) -> Board:
    result = await session.execute(
        select(Board).where(
            Board.board_type == board_type,
            Board.coverage_start == coverage_start,
            Board.coverage_end == coverage_end,
        )
    )
    board = result.scalar_one_or_none()
    if board:
        board.generated_for_date = generated_for_date
        board.payload = payload
        board.source_artifact_ids = source_artifact_ids or []
        board.excluded_artifact_ids = excluded_artifact_ids or []
        board.status = status
        if discord_channel_name is not None:
            board.discord_channel_name = discord_channel_name
        if discord_message_id is not None:
            board.discord_message_id = discord_message_id
        board.updated_at = _utcnow()
        await session.commit()
        await session.refresh(board)
        return board

    board = Board(
        board_type=board_type,
        generated_for_date=generated_for_date,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        payload=payload,
        source_artifact_ids=source_artifact_ids or [],
        excluded_artifact_ids=excluded_artifact_ids or [],
        status=status,
        discord_channel_name=discord_channel_name,
        discord_message_id=discord_message_id,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(board)
    await session.commit()
    await session.refresh(board)
    return board


async def get_board(session: AsyncSession, board_id: uuid.UUID) -> Board | None:
    return await session.get(Board, board_id)


async def get_board_by_window(
    session: AsyncSession,
    *,
    board_type: str,
    coverage_start: datetime,
    coverage_end: datetime,
) -> Board | None:
    result = await session.execute(
        select(Board).where(
            Board.board_type == board_type,
            Board.coverage_start == coverage_start,
            Board.coverage_end == coverage_end,
        )
    )
    return result.scalar_one_or_none()


async def get_latest_board(
    session: AsyncSession,
    *,
    board_type: str,
    generated_for_date: date | None = None,
) -> Board | None:
    query = select(Board).where(Board.board_type == board_type).order_by(Board.generated_for_date.desc(), Board.updated_at.desc())
    if generated_for_date:
        query = query.where(Board.generated_for_date == generated_for_date)
    result = await session.execute(query.limit(1))
    return result.scalar_one_or_none()


async def list_boards(
    session: AsyncSession,
    *,
    board_type: str | None = None,
    limit: int = 30,
) -> list[Board]:
    query = select(Board)
    if board_type:
        query = query.where(Board.board_type == board_type)
    query = query.order_by(Board.generated_for_date.desc(), Board.updated_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def create_journal_entry(session: AsyncSession, **kwargs) -> JournalEntry:
    kwargs["tags"] = normalize_tags(kwargs.get("tags"))
    kwargs.setdefault("evidence_refs", [])
    kwargs.setdefault("source_links", [])
    kwargs.setdefault("subject_type", "topic")
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


async def list_recent_sync_runs(session: AsyncSession, *, limit: int = 25) -> list[SyncRun]:
    result = await session.execute(
        select(SyncRun)
        .join(SyncSource, SyncRun.sync_source_id == SyncSource.id)
        .order_by(SyncRun.started_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_artifact_interpretations(
    session: AsyncSession,
    *,
    validation_status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    latest_class = (
        select(
            Classification.id.label("classification_id"),
            Classification.artifact_id.label("artifact_id"),
            Classification.category,
            Classification.confidence,
            Classification.capture_intent,
            Classification.intent_confidence,
            Classification.validation_status,
            Classification.quality_issues,
            Classification.eligible_for_boards,
            Classification.eligible_for_project_state,
            Classification.tags,
            Classification.created_at.label("classified_at"),
        )
        .distinct(Classification.artifact_id)
        .order_by(Classification.artifact_id, Classification.created_at.desc())
        .subquery()
    )

    query = (
        select(
            Artifact,
            latest_class.c.classification_id,
            latest_class.c.category,
            latest_class.c.confidence,
            latest_class.c.capture_intent,
            latest_class.c.intent_confidence,
            latest_class.c.validation_status,
            latest_class.c.quality_issues,
            latest_class.c.eligible_for_boards,
            latest_class.c.eligible_for_project_state,
            latest_class.c.tags,
            latest_class.c.classified_at,
        )
        .outerjoin(latest_class, latest_class.c.artifact_id == Artifact.id)
        .order_by(Artifact.created_at.desc())
        .limit(limit)
    )
    if validation_status:
        query = query.where(latest_class.c.validation_status == validation_status)

    result = await session.execute(query)
    rows: list[dict] = []
    for row in result.all():
        artifact = row[0]
        rows.append(
            {
                "artifact": artifact,
                "classification_id": row.classification_id,
                "category": row.category,
                "confidence": row.confidence,
                "capture_intent": row.capture_intent,
                "intent_confidence": row.intent_confidence,
                "validation_status": row.validation_status,
                "quality_issues": row.quality_issues or [],
                "eligible_for_boards": row.eligible_for_boards,
                "eligible_for_project_state": row.eligible_for_project_state,
                "tags": row.tags or [],
                "classified_at": row.classified_at,
            }
        )
    return rows


async def get_artifact_interpretation(session: AsyncSession, artifact_id: uuid.UUID) -> dict | None:
    latest_class = await get_latest_classification(session, artifact_id)
    artifact = await get_artifact(session, artifact_id)
    if not artifact:
        return None
    return {
        "artifact": artifact,
        "classification_id": str(latest_class.id) if latest_class else None,
        "category": latest_class.category if latest_class else None,
        "confidence": latest_class.confidence if latest_class else None,
        "capture_intent": latest_class.capture_intent if latest_class else None,
        "intent_confidence": latest_class.intent_confidence if latest_class else None,
        "validation_status": latest_class.validation_status if latest_class else None,
        "quality_issues": list(latest_class.quality_issues or []) if latest_class else [],
        "eligible_for_boards": latest_class.eligible_for_boards if latest_class else False,
        "eligible_for_project_state": latest_class.eligible_for_project_state if latest_class else False,
        "tags": list(latest_class.tags or []) if latest_class else [],
        "classified_at": latest_class.created_at if latest_class else None,
    }


async def list_artifacts_for_window(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    eligible_for_boards: bool | None = None,
    eligible_for_project_state: bool | None = None,
    validation_status: str | None = None,
    limit: int = 250,
) -> list[dict]:
    final_class = (
        select(Classification)
        .where(Classification.is_final == True)
        .subquery()
    )
    query = (
        select(
            Artifact,
            final_class.c.id.label("classification_id"),
            final_class.c.category,
            final_class.c.confidence,
            final_class.c.capture_intent,
            final_class.c.intent_confidence,
            final_class.c.validation_status,
            final_class.c.quality_issues,
            final_class.c.eligible_for_boards,
            final_class.c.eligible_for_project_state,
            final_class.c.tags,
            func.coalesce(SourceItem.happened_at, Artifact.created_at).label("event_time"),
            SourceItem.id.label("source_item_id"),
        )
        .join(final_class, final_class.c.artifact_id == Artifact.id)
        .outerjoin(SourceItem, SourceItem.artifact_id == Artifact.id)
        .where(
            func.coalesce(SourceItem.happened_at, Artifact.created_at) >= start,
            func.coalesce(SourceItem.happened_at, Artifact.created_at) <= end,
        )
        .order_by(Artifact.created_at.asc())
        .limit(limit)
    )
    if validation_status:
        query = query.where(final_class.c.validation_status == validation_status)
    if eligible_for_boards is not None:
        query = query.where(final_class.c.eligible_for_boards == eligible_for_boards)
    if eligible_for_project_state is not None:
        query = query.where(final_class.c.eligible_for_project_state == eligible_for_project_state)

    result = await session.execute(query)
    return [
        {
            "artifact": row[0],
            "classification_id": row.classification_id,
            "category": row.category,
            "confidence": row.confidence,
            "capture_intent": row.capture_intent,
            "intent_confidence": row.intent_confidence,
            "validation_status": row.validation_status,
            "quality_issues": row.quality_issues or [],
            "eligible_for_boards": row.eligible_for_boards,
            "eligible_for_project_state": row.eligible_for_project_state,
            "tags": row.tags or [],
            "event_time": row.event_time,
            "source_item_id": str(row.source_item_id) if row.source_item_id else None,
        }
        for row in result.all()
    ]


async def list_story_events(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID | None = None,
    subject_ref: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    ascending: bool = False,
) -> list[JournalEntry]:
    query = select(JournalEntry)
    if project_note_id:
        query = query.where(JournalEntry.project_note_id == project_note_id)
    if subject_ref:
        lowered = subject_ref.lower()
        query = query.where(
            or_(
                func.lower(JournalEntry.subject_ref) == lowered,
                func.lower(JournalEntry.title).contains(lowered),
                func.lower(func.coalesce(JournalEntry.summary, "")).contains(lowered),
            )
        )
    if since:
        query = query.where(JournalEntry.happened_at >= since)
    if until:
        query = query.where(JournalEntry.happened_at <= until)
    ordering = (
        JournalEntry.happened_at.asc(),
        JournalEntry.created_at.asc(),
    ) if ascending else (
        JournalEntry.happened_at.desc(),
        JournalEntry.created_at.desc(),
    )
    query = query.order_by(*ordering).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def find_story_subjects(session: AsyncSession, phrase: str, limit: int = 10) -> list[JournalEntry]:
    lowered = (phrase or "").strip().lower()
    if not lowered:
        return []

    result = await session.execute(
        select(JournalEntry)
        .where(
            or_(
                func.lower(func.coalesce(JournalEntry.subject_ref, "")).contains(lowered),
                func.lower(JournalEntry.title).contains(lowered),
                func.lower(func.coalesce(JournalEntry.summary, "")).contains(lowered),
            )
        )
        .order_by(JournalEntry.happened_at.desc(), JournalEntry.created_at.desc())
        .limit(limit)
    )
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


async def get_source_item_by_external_id(
    session: AsyncSession,
    *,
    sync_source_id: uuid.UUID,
    external_id: str,
) -> SourceItem | None:
    result = await session.execute(
        select(SourceItem).where(
            SourceItem.sync_source_id == sync_source_id,
            SourceItem.external_id == external_id,
        )
    )
    return result.scalar_one_or_none()


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


async def get_latest_oauth_credential(
    session: AsyncSession,
    provider: str,
    *,
    account_email: str | None = None,
) -> OAuthCredential | None:
    query = select(OAuthCredential).where(OAuthCredential.provider == provider)
    if account_email:
        query = query.where(OAuthCredential.account_email == account_email)
    query = query.order_by(OAuthCredential.updated_at.desc(), OAuthCredential.created_at.desc()).limit(1)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def upsert_oauth_credential(
    session: AsyncSession,
    *,
    provider: str,
    account_email: str | None = None,
    scopes: list[str] | None = None,
    encrypted_refresh_token: str | None = None,
    encrypted_access_token: str | None = None,
    expires_at: datetime | None = None,
    metadata_: dict | None = None,
) -> OAuthCredential:
    credential = await get_latest_oauth_credential(session, provider, account_email=account_email)
    if credential:
        credential.account_email = account_email
        credential.scopes = scopes or []
        if encrypted_refresh_token is not None:
            credential.encrypted_refresh_token = encrypted_refresh_token
        if encrypted_access_token is not None:
            credential.encrypted_access_token = encrypted_access_token
        credential.expires_at = expires_at
        credential.metadata_ = metadata_ or {}
        credential.updated_at = _utcnow()
        await session.commit()
        await session.refresh(credential)
        return credential

    credential = OAuthCredential(
        provider=provider,
        account_email=account_email,
        scopes=scopes or [],
        encrypted_refresh_token=encrypted_refresh_token,
        encrypted_access_token=encrypted_access_token,
        expires_at=expires_at,
        metadata_=metadata_ or {},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(credential)
    await session.commit()
    await session.refresh(credential)
    return credential


async def delete_oauth_credentials(session: AsyncSession, provider: str) -> int:
    result = await session.execute(delete(OAuthCredential).where(OAuthCredential.provider == provider))
    await session.commit()
    return int(result.rowcount or 0)


async def get_protected_content(
    session: AsyncSession,
    *,
    source_type: str,
    source_ref: str,
    content_kind: str = "body",
) -> ProtectedContent | None:
    result = await session.execute(
        select(ProtectedContent).where(
            ProtectedContent.source_type == source_type,
            ProtectedContent.source_ref == source_ref,
            ProtectedContent.content_kind == content_kind,
        )
    )
    return result.scalar_one_or_none()


async def upsert_protected_content(
    session: AsyncSession,
    *,
    source_type: str,
    source_ref: str,
    content_kind: str,
    ciphertext: str,
    nonce: str,
    checksum: str,
    preview_text: str | None = None,
    metadata_: dict | None = None,
) -> ProtectedContent:
    protected = await get_protected_content(
        session,
        source_type=source_type,
        source_ref=source_ref,
        content_kind=content_kind,
    )
    values = {
        "ciphertext": ciphertext,
        "nonce": nonce,
        "checksum": checksum,
        "preview_text": preview_text,
        "metadata_": metadata_ or {},
        "updated_at": _utcnow(),
    }
    if protected:
        for key, value in values.items():
            setattr(protected, key, value)
        await session.commit()
        await session.refresh(protected)
        return protected

    protected = ProtectedContent(
        source_type=source_type,
        source_ref=source_ref,
        content_kind=content_kind,
        created_at=_utcnow(),
        **values,
    )
    session.add(protected)
    await session.commit()
    await session.refresh(protected)
    return protected


async def get_voice_profile(session: AsyncSession, profile_name: str = "ahmad-default") -> VoiceProfile | None:
    result = await session.execute(select(VoiceProfile).where(VoiceProfile.profile_name == profile_name))
    return result.scalar_one_or_none()


async def upsert_voice_profile(
    session: AsyncSession,
    *,
    profile_name: str,
    summary: str | None,
    traits: dict | None,
    style_anchors: list[dict] | None,
    source_refs: list[dict] | None,
    metadata_: dict | None = None,
) -> VoiceProfile:
    profile = await get_voice_profile(session, profile_name)
    values = {
        "summary": summary,
        "traits": traits or {},
        "style_anchors": style_anchors or [],
        "source_refs": source_refs or [],
        "metadata_": metadata_ or {},
        "last_refreshed_at": _utcnow(),
        "updated_at": _utcnow(),
    }
    if profile:
        profile.version += 1
        for key, value in values.items():
            setattr(profile, key, value)
        await session.commit()
        await session.refresh(profile)
        return profile

    profile = VoiceProfile(
        profile_name=profile_name,
        version=1,
        created_at=_utcnow(),
        **values,
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def upsert_conversation_session(
    session: AsyncSession,
    *,
    source_item_id: uuid.UUID,
    agent_kind: str,
    session_id: str,
    project_note_id: uuid.UUID | None = None,
    parent_session_id: str | None = None,
    cwd: str | None = None,
    title_hint: str | None = None,
    transcript_blob_ref: str | None = None,
    transcript_excerpt: str | None = None,
    participants: list[str] | None = None,
    turn_count: int = 0,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    metadata_: dict | None = None,
) -> tuple[ConversationSession, bool]:
    result = await session.execute(
        select(ConversationSession).where(ConversationSession.source_item_id == source_item_id)
    )
    conversation = result.scalar_one_or_none()
    if conversation:
        conversation.project_note_id = project_note_id
        conversation.agent_kind = agent_kind
        conversation.session_id = session_id
        conversation.parent_session_id = parent_session_id
        conversation.cwd = cwd
        conversation.title_hint = title_hint
        conversation.transcript_blob_ref = transcript_blob_ref
        conversation.transcript_excerpt = transcript_excerpt
        conversation.participants = participants or []
        conversation.turn_count = turn_count
        conversation.started_at = started_at
        conversation.ended_at = ended_at
        conversation.metadata_ = metadata_ or {}
        conversation.updated_at = _utcnow()
        await session.commit()
        await session.refresh(conversation)
        return conversation, False

    conversation = ConversationSession(
        source_item_id=source_item_id,
        project_note_id=project_note_id,
        agent_kind=agent_kind,
        session_id=session_id,
        parent_session_id=parent_session_id,
        cwd=cwd,
        title_hint=title_hint,
        transcript_blob_ref=transcript_blob_ref,
        transcript_excerpt=transcript_excerpt,
        participants=participants or [],
        turn_count=turn_count,
        started_at=started_at,
        ended_at=ended_at,
        metadata_=metadata_ or {},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return conversation, True


async def list_conversation_sessions(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: int = 50,
) -> list[ConversationSession]:
    query = select(ConversationSession)
    if project_note_id:
        query = query.where(ConversationSession.project_note_id == project_note_id)
    if since:
        query = query.where(
            or_(
                ConversationSession.ended_at >= since,
                ConversationSession.updated_at >= since,
            )
        )
    query = query.order_by(
        ConversationSession.ended_at.desc().nullslast(),
        ConversationSession.updated_at.desc(),
    ).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_project_state_snapshot(
    session: AsyncSession,
    project_note_id: uuid.UUID,
) -> ProjectStateSnapshot | None:
    result = await session.execute(
        select(ProjectStateSnapshot).where(ProjectStateSnapshot.project_note_id == project_note_id)
    )
    return result.scalar_one_or_none()


async def upsert_project_state_snapshot(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID,
    active_score: float,
    status: str,
    confidence: float,
    implemented: str | None = None,
    remaining: str | None = None,
    blockers: list[str] | None = None,
    risks: list[str] | None = None,
    holes: list[str] | None = None,
    what_changed: str | None = None,
    why_active: str | None = None,
    why_not_active: str | None = None,
    last_signal_at: datetime | None = None,
    feature_scores: dict | None = None,
    metadata_: dict | None = None,
    manual_state: str | None = None,
) -> ProjectStateSnapshot:
    snapshot = await get_project_state_snapshot(session, project_note_id)
    values = {
        "active_score": active_score,
        "status": status,
        "confidence": confidence,
        "implemented": implemented,
        "remaining": remaining,
        "blockers": blockers or [],
        "risks": risks or [],
        "holes": holes or [],
        "what_changed": what_changed,
        "why_active": why_active,
        "why_not_active": why_not_active,
        "last_signal_at": last_signal_at,
        "feature_scores": feature_scores or {},
        "metadata_": metadata_ or {},
        "updated_at": _utcnow(),
    }
    if manual_state is not None:
        values["manual_state"] = manual_state
    if snapshot:
        for key, value in values.items():
            setattr(snapshot, key, value)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot

    snapshot = ProjectStateSnapshot(
        project_note_id=project_note_id,
        manual_state=manual_state or "normal",
        created_at=_utcnow(),
        **values,
    )
    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)
    return snapshot


async def list_project_state_snapshots(
    session: AsyncSession,
    *,
    statuses: list[str] | None = None,
    limit: int = 25,
) -> list[ProjectStateSnapshot]:
    query = select(ProjectStateSnapshot)
    if statuses:
        query = query.where(ProjectStateSnapshot.status.in_(statuses))
    query = query.order_by(ProjectStateSnapshot.active_score.desc(), ProjectStateSnapshot.updated_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def set_project_manual_state(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID,
    manual_state: str,
) -> ProjectStateSnapshot:
    snapshot = await get_project_state_snapshot(session, project_note_id)
    if snapshot:
        snapshot.manual_state = manual_state
        snapshot.updated_at = _utcnow()
        await session.commit()
        await session.refresh(snapshot)
        return snapshot
    return await upsert_project_state_snapshot(
        session,
        project_note_id=project_note_id,
        active_score=0.0,
        status="uncertain",
        confidence=0.0,
        manual_state=manual_state,
    )


async def upsert_story_connection(
    session: AsyncSession,
    *,
    source_ref: str,
    target_ref: str,
    relation: str = "co_signal",
    source_project_note_id: uuid.UUID | None = None,
    target_project_note_id: uuid.UUID | None = None,
    weight: float = 0.0,
    evidence_count: int = 0,
    metadata_: dict | None = None,
) -> StoryConnection:
    result = await session.execute(
        select(StoryConnection).where(
            StoryConnection.source_ref == source_ref,
            StoryConnection.target_ref == target_ref,
            StoryConnection.relation == relation,
        )
    )
    connection = result.scalar_one_or_none()
    if connection:
        connection.source_project_note_id = source_project_note_id
        connection.target_project_note_id = target_project_note_id
        connection.weight = weight
        connection.evidence_count = evidence_count
        connection.metadata_ = metadata_ or {}
        connection.updated_at = _utcnow()
        await session.commit()
        await session.refresh(connection)
        return connection

    connection = StoryConnection(
        source_ref=source_ref,
        target_ref=target_ref,
        relation=relation,
        source_project_note_id=source_project_note_id,
        target_project_note_id=target_project_note_id,
        weight=weight,
        evidence_count=evidence_count,
        metadata_=metadata_ or {},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(connection)
    await session.commit()
    await session.refresh(connection)
    return connection


async def list_story_connections(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID | None = None,
    limit: int = 25,
) -> list[StoryConnection]:
    query = select(StoryConnection)
    if project_note_id:
        query = query.where(
            or_(
                StoryConnection.source_project_note_id == project_note_id,
                StoryConnection.target_project_note_id == project_note_id,
            )
        )
    query = query.order_by(StoryConnection.weight.desc(), StoryConnection.updated_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def replace_story_connections_for_project(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID,
    project_ref: str,
    connections: list[dict],
) -> list[StoryConnection]:
    await session.execute(
        delete(StoryConnection).where(
            or_(
                StoryConnection.source_project_note_id == project_note_id,
                StoryConnection.target_project_note_id == project_note_id,
            ),
            StoryConnection.relation == "co_signal",
        )
    )
    await session.commit()

    saved: list[StoryConnection] = []
    for item in connections:
        target_ref = item.get("target_ref")
        if not target_ref or target_ref == project_ref:
            continue
        ordered = sorted([project_ref, target_ref], key=str.lower)
        saved.append(
            await upsert_story_connection(
                session,
                source_ref=ordered[0],
                target_ref=ordered[1],
                relation="co_signal",
                source_project_note_id=project_note_id if ordered[0] == project_ref else item.get("target_project_note_id"),
                target_project_note_id=project_note_id if ordered[1] == project_ref else item.get("target_project_note_id"),
                weight=float(item.get("weight") or 0.0),
                evidence_count=int(item.get("evidence_count") or 0),
                metadata_=item.get("metadata") or {},
            )
        )
    return saved


async def clear_story_connections(session: AsyncSession, *, relation: str = "co_signal") -> None:
    await session.execute(delete(StoryConnection).where(StoryConnection.relation == relation))
    await session.commit()


async def upsert_reminder(
    session: AsyncSession,
    *,
    title: str,
    timezone_name: str,
    recurrence_kind: str,
    recurrence_rule: dict,
    next_fire_at: datetime | None,
    note_id: uuid.UUID | None = None,
    project_note_id: uuid.UUID | None = None,
    body: str | None = None,
    delivery_channel: str = "discord",
    discord_channel_id: str | None = None,
    status: str = "active",
    metadata_: dict | None = None,
) -> Reminder:
    result = await session.execute(
        select(Reminder).where(
            Reminder.title == title,
            Reminder.project_note_id == project_note_id,
            Reminder.delivery_channel == delivery_channel,
            Reminder.status.in_(("active", "paused")),
        )
    )
    reminder = result.scalar_one_or_none()
    values = {
        "body": body,
        "timezone": timezone_name,
        "recurrence_kind": recurrence_kind,
        "recurrence_rule": recurrence_rule,
        "next_fire_at": next_fire_at,
        "note_id": note_id,
        "project_note_id": project_note_id,
        "delivery_channel": delivery_channel,
        "discord_channel_id": discord_channel_id,
        "status": status,
        "metadata_": metadata_ or {},
        "updated_at": _utcnow(),
    }
    if reminder:
        for key, value in values.items():
            setattr(reminder, key, value)
        await session.commit()
        await session.refresh(reminder)
        return reminder

    reminder = Reminder(
        title=title,
        created_at=_utcnow(),
        **values,
    )
    session.add(reminder)
    await session.commit()
    await session.refresh(reminder)
    return reminder


async def list_due_reminders(
    session: AsyncSession,
    *,
    due_before: datetime,
    limit: int = 50,
) -> list[Reminder]:
    result = await session.execute(
        select(Reminder)
        .where(
            Reminder.status == "active",
            Reminder.next_fire_at.is_not(None),
            Reminder.next_fire_at <= due_before,
        )
        .order_by(Reminder.next_fire_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_reminder(session: AsyncSession, reminder_id: uuid.UUID, **kwargs) -> Reminder | None:
    kwargs.setdefault("updated_at", _utcnow())
    await session.execute(update(Reminder).where(Reminder.id == reminder_id).values(**kwargs))
    await session.commit()
    result = await session.execute(select(Reminder).where(Reminder.id == reminder_id))
    return result.scalar_one_or_none()


async def list_reminders(
    session: AsyncSession,
    *,
    status: str = "active",
    limit: int = 50,
) -> list[Reminder]:
    result = await session.execute(
        select(Reminder)
        .where(Reminder.status == status)
        .order_by(Reminder.next_fire_at.asc().nullslast(), Reminder.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_project_reminders(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID,
    status: str = "active",
    limit: int = 50,
) -> list[Reminder]:
    result = await session.execute(
        select(Reminder)
        .where(
            Reminder.project_note_id == project_note_id,
            Reminder.status == status,
        )
        .order_by(Reminder.next_fire_at.asc().nullslast(), Reminder.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_digest_preference(session: AsyncSession, profile_name: str = "default") -> DigestPreference | None:
    result = await session.execute(
        select(DigestPreference).where(DigestPreference.profile_name == profile_name)
    )
    return result.scalar_one_or_none()


async def upsert_digest_preference(
    session: AsyncSession,
    *,
    profile_name: str,
    timezone_name: str,
    sections: dict,
    metadata_: dict | None = None,
) -> DigestPreference:
    preference = await get_digest_preference(session, profile_name)
    if preference:
        preference.timezone = timezone_name
        preference.sections = sections
        preference.metadata_ = metadata_ or {}
        preference.updated_at = _utcnow()
        await session.commit()
        await session.refresh(preference)
        return preference

    preference = DigestPreference(
        profile_name=profile_name,
        timezone=timezone_name,
        sections=sections,
        metadata_=metadata_ or {},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(preference)
    await session.commit()
    await session.refresh(preference)
    return preference


async def create_retrieval_trace(
    session: AsyncSession,
    *,
    trace_id: uuid.UUID,
    question: str,
    resolved_mode: str,
    resolved_intent: str,
    failure_stage: str | None = None,
    evidence_quality: dict | None = None,
    used_exact_match: bool = False,
    used_project_snapshot: bool = False,
    used_vector_search: bool = False,
    used_web: bool = False,
    payload: dict | None = None,
) -> RetrievalTrace:
    trace = RetrievalTrace(
        id=trace_id,
        question=question,
        resolved_mode=resolved_mode,
        resolved_intent=resolved_intent,
        failure_stage=failure_stage,
        evidence_quality=evidence_quality or {},
        used_exact_match=used_exact_match,
        used_project_snapshot=used_project_snapshot,
        used_vector_search=used_vector_search,
        used_web=used_web,
        payload=payload or {},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(trace)
    await session.commit()
    await session.refresh(trace)
    return trace


async def update_retrieval_trace(
    session: AsyncSession,
    trace_id: uuid.UUID,
    **kwargs,
) -> RetrievalTrace | None:
    kwargs.setdefault("updated_at", _utcnow())
    await session.execute(update(RetrievalTrace).where(RetrievalTrace.id == trace_id).values(**kwargs))
    await session.commit()
    result = await session.execute(select(RetrievalTrace).where(RetrievalTrace.id == trace_id))
    return result.scalar_one_or_none()


async def get_retrieval_trace(session: AsyncSession, trace_id: uuid.UUID) -> RetrievalTrace | None:
    return await session.get(RetrievalTrace, trace_id)


async def list_retrieval_traces(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> list[RetrievalTrace]:
    result = await session.execute(
        select(RetrievalTrace).order_by(RetrievalTrace.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def create_eval_run(
    session: AsyncSession,
    *,
    run_name: str,
    status: str = "running",
    summary: dict | None = None,
    metadata_: dict | None = None,
) -> EvalRun:
    eval_run = EvalRun(
        run_name=run_name,
        status=status,
        summary=summary or {},
        metadata_=metadata_ or {},
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(eval_run)
    await session.commit()
    await session.refresh(eval_run)
    return eval_run


async def update_eval_run(session: AsyncSession, eval_run_id: uuid.UUID, **kwargs) -> EvalRun | None:
    kwargs.setdefault("updated_at", _utcnow())
    await session.execute(update(EvalRun).where(EvalRun.id == eval_run_id).values(**kwargs))
    await session.commit()
    result = await session.execute(select(EvalRun).where(EvalRun.id == eval_run_id))
    return result.scalar_one_or_none()


async def list_eval_runs(session: AsyncSession, *, limit: int = 20) -> list[EvalRun]:
    result = await session.execute(
        select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def create_eval_case_result(
    session: AsyncSession,
    *,
    eval_run_id: uuid.UUID,
    case_name: str,
    question: str,
    expected: dict,
    actual: dict,
    status: str,
    score: float,
    notes: str | None = None,
) -> EvalCaseResult:
    case_result = EvalCaseResult(
        eval_run_id=eval_run_id,
        case_name=case_name,
        question=question,
        expected=expected,
        actual=actual,
        status=status,
        score=score,
        notes=notes,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    session.add(case_result)
    await session.commit()
    await session.refresh(case_result)
    return case_result


async def list_eval_case_results(
    session: AsyncSession,
    *,
    eval_run_id: uuid.UUID,
) -> list[EvalCaseResult]:
    result = await session.execute(
        select(EvalCaseResult)
        .where(EvalCaseResult.eval_run_id == eval_run_id)
        .order_by(EvalCaseResult.created_at.asc())
    )
    return list(result.scalars().all())
