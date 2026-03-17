"""Canonical library promotion and read models."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import store
from src.lib.provenance import DERIVED_ENTRY_TYPES, signal_kind_for_artifact, signal_kind_for_event
from src.lib.time import describe_event_time, format_display_datetime
from src.models import Artifact, Board, ConversationSession, JournalEntry, Note, SourceItem

LOW_SIGNAL_ENTRY_TYPES = {
    "context_dump",
    "context_signal_dump",
    "directory_inventory",
    "repo_snapshot",
    "repo_signal_summary",
    "workspace_signal_summary",
    "workspace_landscape_summary",
    "agent_reference_signal",
    "agent_plan_signal",
    "agent_todo_signal",
    "session_summary_generation",
}


def _excerpt(value: str | None, *, limit: int = 320) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    return cleaned if len(cleaned) <= limit else f"{cleaned[:limit - 1].rstrip()}…"


def _normalize_name(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    return "".join(ch if ch.isalnum() else "-" for ch in cleaned).strip("-")


def _note_thread_type(note: Note) -> str:
    mapping = {
        "project": "project",
        "people": "person",
        "idea": "idea",
        "reminder": "reminder",
    }
    return mapping.get(note.category, "topic")


def _note_entity_type(note: Note) -> str:
    mapping = {
        "project": "project",
        "people": "person",
        "idea": "concept",
        "reminder": "reminder",
    }
    return mapping.get(note.category, "topic")


async def _promote_notes(session: AsyncSession) -> dict[str, list[str]]:
    projects = await store.list_notes(session, status="active", limit=300)
    thread_ids_by_project: dict[str, list[str]] = {}
    for note in projects:
        aliases = [note.title]
        if note.category == "project":
            aliases.extend(alias.alias for alias in await store.list_project_aliases(session, project_note_id=note.id, limit=50))
        thread = await store.upsert_thread_record(
            session,
            source_kind="note",
            source_ref=str(note.id),
            thread_type=_note_thread_type(note),
            title=note.title,
            summary=_excerpt(note.content),
            status=note.status,
            priority=note.priority or "medium",
            provenance_kind="direct_human",
            retention_class="hot" if note.category in {"project", "idea", "people"} else "warm",
            project_note_id=note.id if note.category == "project" else None,
            subject_ref=note.title,
            aliases=[alias for alias in aliases if alias],
            metadata_={"category": note.category, "tags": list(note.tags or [])},
            last_event_at=note.updated_at or note.created_at,
        )
        entity = await store.upsert_entity_record(
            session,
            source_kind="note",
            source_ref=str(note.id),
            entity_type=_note_entity_type(note),
            name=note.title,
            normalized_name=_normalize_name(note.title),
            summary=_excerpt(note.content),
            aliases=[alias for alias in aliases if alias],
            thread_ids=[str(thread.id)],
            metadata_={"category": note.category, "tags": list(note.tags or [])},
            last_seen_at=note.updated_at or note.created_at,
        )
        thread_ids_by_project[str(note.id)] = [str(thread.id), str(entity.id)]
    return thread_ids_by_project


async def _promote_artifacts(session: AsyncSession) -> None:
    items = await store.list_artifact_interpretations(session, limit=300)
    for item in items:
        artifact = item["artifact"]
        await store.upsert_evidence_record(
            session,
            source_kind="artifact",
            source_ref=str(artifact.id),
            artifact_id=artifact.id,
            title=artifact.summary or artifact.content_type.title(),
            summary=_excerpt(item.get("category") or artifact.summary or artifact.content_type),
            excerpt=_excerpt(artifact.raw_text),
            content_kind=artifact.content_type,
            source_type=artifact.source,
            provenance_kind=signal_kind_for_artifact(
                source=artifact.source,
                capture_context=(artifact.metadata_ or {}).get("capture_context"),
            ),
            retention_class="warm",
            content_hash=artifact.blob_hash,
            is_sensitive=bool((artifact.metadata_ or {}).get("sensitive")),
            metadata_={
                "capture_intent": item.get("capture_intent"),
                "category": item.get("category"),
                "validation_status": item.get("validation_status"),
                "quality_issues": item.get("quality_issues") or [],
            },
            event_time=artifact.created_at,
        )


async def _promote_source_items(session: AsyncSession) -> None:
    result = await session.execute(
        select(SourceItem).order_by(SourceItem.created_at.desc()).limit(250)
    )
    for source_item in result.scalars().all():
        await store.upsert_evidence_record(
            session,
            source_kind="source_item",
            source_ref=str(source_item.id),
            artifact_id=source_item.artifact_id,
            source_item_id=source_item.id,
            project_note_id=source_item.project_note_id,
            title=source_item.title,
            summary=_excerpt(source_item.summary),
            excerpt=_excerpt(str((source_item.payload or {}).get("body_markdown") or (source_item.payload or {}).get("summary") or "")),
            content_kind=str((source_item.payload or {}).get("entry_type") or "text"),
            source_type=str((source_item.payload or {}).get("source_type") or "source_item"),
            provenance_kind="direct_sync",
            retention_class="warm",
            content_hash=source_item.content_hash,
            is_sensitive=bool((source_item.payload or {}).get("is_sensitive")),
            metadata_={"external_url": source_item.external_url},
            event_time=source_item.happened_at or source_item.created_at,
        )


async def _promote_journal_entries(session: AsyncSession, thread_ids_by_project: dict[str, list[str]]) -> None:
    entries = await store.list_recent_activity(session, limit=300)
    for entry in entries:
        if entry.entry_type in LOW_SIGNAL_ENTRY_TYPES:
            continue
        project_refs = thread_ids_by_project.get(str(entry.project_note_id), []) if entry.project_note_id else []
        title = entry.title
        summary = _excerpt(entry.summary or entry.body_markdown)
        body = _excerpt(entry.body_markdown, limit=2000)
        base_values = {
            "project_note_id": entry.project_note_id,
            "provenance_kind": signal_kind_for_event(entry_type=entry.entry_type, actor_type=entry.actor_type),
            "thread_ids": project_refs[:1],
            "entity_ids": project_refs[1:2],
            "metadata_": {
                "entry_type": entry.entry_type,
                "actor_type": entry.actor_type,
                "actor_name": entry.actor_name,
                "tags": list(entry.tags or []),
                "decision": entry.decision,
                "constraint": entry.constraint,
                "outcome": entry.outcome,
                "open_question": entry.open_question,
            },
            "event_time": entry.happened_at or entry.created_at,
        }
        if entry.entry_type in DERIVED_ENTRY_TYPES:
            await store.upsert_synthesis_record(
                session,
                source_kind="journal_entry",
                source_ref=str(entry.id),
                synthesis_type=entry.entry_type,
                title=title,
                summary=summary,
                body=body,
                certainty_class="plausible_inference",
                source_refs=list(entry.evidence_refs or []),
                **base_values,
            )
            continue
        observation_type = "decision" if entry.decision else "question" if entry.open_question else "fact"
        await store.upsert_observation_record(
            session,
            source_kind="journal_entry",
            source_ref=str(entry.id),
            observation_type=observation_type,
            title=title,
            summary=summary,
            body=body,
            certainty=0.92 if entry.actor_type in {"human", "agent"} else 0.78,
            actor=entry.actor_name,
            evidence_ids=list(entry.evidence_refs or []),
            retention_class="hot" if entry.entry_type in {"progress_update", "session_closeout", "decision"} else "warm",
            **base_values,
        )


async def _promote_conversation_sessions(session: AsyncSession, thread_ids_by_project: dict[str, list[str]]) -> None:
    result = await session.execute(
        select(ConversationSession).order_by(ConversationSession.updated_at.desc()).limit(200)
    )
    for conversation in result.scalars().all():
        thread_refs = thread_ids_by_project.get(str(conversation.project_note_id), []) if conversation.project_note_id else []
        await store.upsert_episode_record(
            session,
            source_kind="conversation_session",
            source_ref=str(conversation.id),
            project_note_id=conversation.project_note_id,
            episode_type="conversation_session",
            title=conversation.title_hint or f"{conversation.agent_kind} session",
            summary=_excerpt(conversation.transcript_excerpt),
            provenance_kind="direct_agent",
            retention_class="hot",
            participants=list(conversation.participants or []),
            thread_ids=thread_refs[:1],
            entity_ids=thread_refs[1:2],
            observation_ids=[],
            source_refs=[str(conversation.source_item_id)] if conversation.source_item_id else [],
            metadata_={
                "agent_kind": conversation.agent_kind,
                "session_id": conversation.session_id,
                "cwd": conversation.cwd,
                "turn_count": conversation.turn_count,
            },
            coverage_start=conversation.started_at or conversation.created_at,
            coverage_end=conversation.ended_at or conversation.updated_at,
        )


async def _promote_boards(session: AsyncSession, thread_ids_by_project: dict[str, list[str]]) -> None:
    result = await session.execute(
        select(Board).order_by(Board.created_at.desc()).limit(60)
    )
    for board in result.scalars().all():
        await store.upsert_synthesis_record(
            session,
            source_kind="board",
            source_ref=str(board.id),
            project_note_id=None,
            synthesis_type=f"{board.board_type}_board",
            title=f"{board.board_type.title()} board for {board.generated_for_date.isoformat()}",
            summary=_excerpt(str((board.payload or {}).get("story") or (board.payload or {}).get("summary") or "")),
            body=_excerpt(str(board.payload), limit=4000),
            certainty_class="grounded_observation",
            provenance_kind="derived_system",
            thread_ids=[],
            entity_ids=[],
            source_refs=[str(value) for value in list(board.source_artifact_ids or [])[:12]],
            metadata_={
                "coverage_start": board.coverage_start.isoformat(),
                "coverage_end": board.coverage_end.isoformat(),
                "generated_for_date": board.generated_for_date.isoformat(),
                "board_type": board.board_type,
            },
            event_time=board.created_at,
        )


async def sync_canonical_library(session: AsyncSession) -> dict[str, int]:
    thread_ids_by_project = await _promote_notes(session)
    await _promote_artifacts(session)
    await _promote_source_items(session)
    await _promote_journal_entries(session, thread_ids_by_project)
    await _promote_conversation_sessions(session, thread_ids_by_project)
    await _promote_boards(session, thread_ids_by_project)
    return {
        "threads": len(await store.list_thread_records(session, limit=500)),
        "entities": len(await store.list_entity_records(session, limit=500)),
        "observations": len(await store.list_observation_records(session, limit=500)),
        "episodes": len(await store.list_episode_records(session, limit=500)),
        "syntheses": len(await store.list_synthesis_records(session, limit=500)),
        "evidence": len(await store.list_evidence_records(session, limit=500)),
    }


def _library_item(
    *,
    record_id: str,
    record_kind: str,
    facet: str,
    title: str,
    summary: str | None,
    provenance_kind: str,
    event_time,
    source_type: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": record_id,
        "record_kind": record_kind,
        "facet": facet,
        "title": title,
        "summary": summary or "",
        "provenance_kind": provenance_kind,
        "source_type": source_type,
        "metadata": metadata or {},
        "happened_at_local": format_display_datetime(event_time),
        **describe_event_time(event_time),
    }


async def build_library_catalog(
    session: AsyncSession,
    *,
    q: str | None = None,
    record_kind: str | None = None,
    facet: str | None = None,
    limit: int = 200,
    sync: bool = False,
) -> list[dict[str, Any]]:
    if sync:
        await sync_canonical_library(session)
    items: list[dict[str, Any]] = []
    if record_kind in (None, "", "thread"):
        for record in await store.list_thread_records(session, q=q, limit=limit):
            items.append(
                _library_item(
                    record_id=str(record.id),
                    record_kind="thread",
                    facet=record.thread_type,
                    title=record.title,
                    summary=record.summary,
                    provenance_kind=record.provenance_kind,
                    event_time=record.last_event_at or record.updated_at,
                    source_type=record.source_kind,
                    metadata={"status": record.status, "subject_ref": record.subject_ref, "aliases": record.aliases or []},
                )
            )
    if record_kind in (None, "", "episode"):
        for record in await store.list_episode_records(session, q=q, limit=limit):
            items.append(
                _library_item(
                    record_id=str(record.id),
                    record_kind="episode",
                    facet=record.episode_type,
                    title=record.title,
                    summary=record.summary,
                    provenance_kind=record.provenance_kind,
                    event_time=record.coverage_end or record.coverage_start or record.updated_at,
                    source_type=record.source_kind,
                    metadata={"participants": record.participants or [], "thread_ids": record.thread_ids or []},
                )
            )
    if record_kind in (None, "", "observation"):
        for record in await store.list_observation_records(session, q=q, limit=limit):
            items.append(
                _library_item(
                    record_id=str(record.id),
                    record_kind="observation",
                    facet=record.observation_type,
                    title=record.title,
                    summary=record.summary or record.body,
                    provenance_kind=record.provenance_kind,
                    event_time=record.event_time or record.updated_at,
                    source_type=record.source_kind,
                    metadata={"thread_ids": record.thread_ids or [], "certainty": record.certainty},
                )
            )
    if record_kind in (None, "", "entity"):
        for record in await store.list_entity_records(session, q=q, limit=limit):
            items.append(
                _library_item(
                    record_id=str(record.id),
                    record_kind="entity",
                    facet=record.entity_type,
                    title=record.name,
                    summary=record.summary,
                    provenance_kind="direct_sync",
                    event_time=record.last_seen_at or record.updated_at,
                    source_type=record.source_kind,
                    metadata={"aliases": record.aliases or [], "thread_ids": record.thread_ids or []},
                )
            )
    if record_kind in (None, "", "synthesis"):
        for record in await store.list_synthesis_records(session, q=q, limit=limit):
            items.append(
                _library_item(
                    record_id=str(record.id),
                    record_kind="synthesis",
                    facet=record.synthesis_type,
                    title=record.title,
                    summary=record.summary or record.body,
                    provenance_kind=record.provenance_kind,
                    event_time=record.event_time or record.updated_at,
                    source_type=record.source_kind,
                    metadata={"certainty_class": record.certainty_class, "thread_ids": record.thread_ids or []},
                )
            )
    if record_kind in (None, "", "evidence"):
        for record in await store.list_evidence_records(session, q=q, limit=limit):
            items.append(
                _library_item(
                    record_id=str(record.id),
                    record_kind="evidence",
                    facet=record.content_kind,
                    title=record.title,
                    summary=record.summary or record.excerpt,
                    provenance_kind=record.provenance_kind,
                    event_time=record.event_time or record.updated_at,
                    source_type=record.source_type,
                    metadata={"retention_class": record.retention_class, "is_sensitive": record.is_sensitive},
                )
            )
    if facet:
        lowered = facet.strip().lower()
        items = [item for item in items if item["facet"].lower() == lowered]
    items.sort(key=lambda item: item.get("event_time_utc") or "", reverse=True)
    return items[:limit]


async def build_final_stored_data(session: AsyncSession) -> dict[str, Any]:
    counts = {
        "threads": len(await store.list_thread_records(session, limit=500)),
        "entities": len(await store.list_entity_records(session, limit=500)),
        "observations": len(await store.list_observation_records(session, limit=500)),
        "episodes": len(await store.list_episode_records(session, limit=500)),
        "syntheses": len(await store.list_synthesis_records(session, limit=500)),
        "evidence": len(await store.list_evidence_records(session, limit=500)),
    }
    recent_items = await build_library_catalog(session, limit=40, sync=False)
    return {
        "counts": counts,
        "items": recent_items,
    }
