"""One-time and repair-oriented backfill for structured conversation sessions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import store
from src.models import SourceItem, SyncSource
from src.services.identity import resolve_project
from src.services.project_state import recompute_project_states

CONVERSATION_ENTRY_TYPES = {"conversation_session", "session_closeout"}
DEFAULT_SOURCE_TYPES = ("codex_history", "claude_history", "agent")


def _parse_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    return datetime.fromisoformat(cleaned)


async def backfill_conversation_sessions(
    session: AsyncSession,
    *,
    source_types: tuple[str, ...] | list[str] | None = None,
) -> dict:
    wanted_source_types = tuple(source_types or DEFAULT_SOURCE_TYPES)
    rows = await session.execute(
        select(SourceItem, SyncSource.source_type)
        .join(SyncSource, SourceItem.sync_source_id == SyncSource.id)
        .where(SyncSource.source_type.in_(wanted_source_types))
        .order_by(SourceItem.happened_at.asc().nullslast(), SourceItem.created_at.asc())
    )

    created = 0
    updated = 0
    scanned = 0
    touched_project_ids: set[uuid.UUID] = set()

    for item, source_type in rows.all():
        payload = dict(item.payload or {})
        if payload.get("entry_type") not in CONVERSATION_ENTRY_TYPES:
            continue

        scanned += 1
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}

        project_note_id = item.project_note_id
        if not project_note_id:
            project = await resolve_project(
                session,
                project_hint=payload.get("project_ref"),
                cwd=metadata.get("cwd"),
                source_refs=[item.title, item.external_url, metadata.get("title_hint")],
                create_if_missing=False,
            )
            if project:
                project_note_id = project.id

        _conversation, was_created = await store.upsert_conversation_session(
            session,
            source_item_id=item.id,
            project_note_id=project_note_id,
            agent_kind=str(metadata.get("agent_kind") or source_type),
            session_id=str(metadata.get("session_id") or item.external_id),
            parent_session_id=metadata.get("parent_session_id"),
            cwd=metadata.get("cwd"),
            title_hint=metadata.get("title_hint") or item.title[:240],
            transcript_blob_ref=None,
            transcript_excerpt=(item.summary or payload.get("summary") or "")[:4000] or None,
            participants=list(metadata.get("participants") or []),
            turn_count=int(metadata.get("turn_count") or 0),
            started_at=_parse_optional_datetime(metadata.get("started_at")),
            ended_at=_parse_optional_datetime(metadata.get("ended_at")) or item.happened_at,
            metadata_=metadata,
        )
        if was_created:
            created += 1
        else:
            updated += 1
        if project_note_id:
            touched_project_ids.add(project_note_id)

    if touched_project_ids:
        await recompute_project_states(session, project_note_ids=sorted(touched_project_ids, key=str))

    return {
        "status": "completed",
        "source_types": list(wanted_source_types),
        "items_scanned": scanned,
        "sessions_created": created,
        "sessions_updated": updated,
        "projects_touched": len(touched_project_ids),
    }
