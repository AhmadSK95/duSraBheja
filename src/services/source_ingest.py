"""Shared ingestion flow for external source entries."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.collector.agent_history import redact_text
from src.lib import store
from src.lib.crypto import encrypt_text
from src.services.identity import ensure_project_aliases, infer_project_from_text, resolve_project
from src.services.indexing import index_artifact
from src.services.project_state import recompute_project_states
from src.services.story import publish_story_entry


def _parse_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    return datetime.fromisoformat(cleaned)


async def ingest_source_entries(
    session: AsyncSession,
    *,
    source_type: str,
    source_name: str,
    mode: str,
    entries: list[dict],
    device_name: str | None = None,
    emit_sync_event: bool = True,
) -> dict:
    from src.services.sync import _extract_story_fields, _publish_sync_event, _trigger_story_pulse

    sync_source = await store.upsert_sync_source(
        session,
        source_type=source_type,
        name=source_name,
        status="active",
        config={"device_name": device_name} if device_name else {},
    )
    sync_run = await store.start_sync_run(session, sync_source_id=sync_source.id, mode=mode)

    imported = 0
    projects_touched: set[str] = set()
    project_note_ids_touched: set[str] = set()

    for entry in entries:
        external_id = str(entry.get("external_id") or uuid.uuid4())
        title = str(entry.get("title") or entry.get("project_ref") or "Source update")
        summary = entry.get("summary")
        raw_body = str(entry.get("raw_body_markdown") or entry.get("body_markdown") or "")
        safe_body = str(entry.get("body_markdown") or summary or title)
        metadata = dict(entry.get("metadata") or {})
        content_hash = entry.get("content_hash")
        happened_at = entry.get("happened_at")
        happened_dt = _parse_optional_datetime(happened_at)

        project_note = await resolve_project(
            session,
            project_hint=entry.get("project_ref"),
            cwd=(entry.get("repo") or {}).get("local_path"),
            repo_name=(entry.get("repo") or {}).get("name"),
            source_refs=[
                title,
                entry.get("external_url"),
                metadata.get("cwd"),
                metadata.get("title_hint"),
            ],
            create_if_missing=bool(entry.get("project_ref")),
        )
        if not project_note and (safe_body or summary):
            project_note = await infer_project_from_text(session, "\n".join(filter(None, [title, summary, safe_body])))

        if project_note:
            projects_touched.add(project_note.title)
            project_note_ids_touched.add(str(project_note.id))
            await ensure_project_aliases(
                session,
                project_note_id=project_note.id,
                title=project_note.title,
                aliases=[
                    entry.get("project_ref"),
                    title,
                    (entry.get("repo") or {}).get("name"),
                    (entry.get("repo") or {}).get("url"),
                    (entry.get("repo") or {}).get("local_path"),
                    metadata.get("cwd"),
                ],
                source_type=source_type,
                source_ref=external_id,
            )

        if project_note and entry.get("repo"):
            repo = entry["repo"]
            await store.upsert_project_repo(
                session,
                project_note_id=project_note.id,
                repo_name=repo.get("name") or project_note.title,
                repo_owner=repo.get("owner"),
                repo_url=repo.get("url"),
                branch=repo.get("branch"),
                local_path=repo.get("local_path"),
                is_primary=repo.get("is_primary", False),
            )

        source_item = await store.get_source_item_by_external_id(
            session,
            sync_source_id=sync_source.id,
            external_id=external_id,
        )
        if source_item and content_hash and source_item.content_hash == content_hash:
            continue

        sensitive = bool(entry.get("is_sensitive") or metadata.get("sensitive"))
        artifact_raw_text = safe_body if sensitive else raw_body
        artifact = await store.create_artifact(
            session,
            content_type="text",
            raw_text=artifact_raw_text,
            summary=title,
            source=source_type,
            metadata_={
                "entry_type": entry.get("entry_type", "context_dump"),
                "device_name": device_name,
                "project_ref": project_note.title if project_note else entry.get("project_ref"),
                "source_type": source_type,
                "sensitive": sensitive,
                "source_metadata": {key: value for key, value in metadata.items() if key != "protected_body_markdown"},
            },
        )
        await store.create_classification(
            session,
            artifact_id=artifact.id,
            category=entry.get("category") or ("project" if project_note else "note"),
            confidence=1.0,
            entities=[],
            tags=entry.get("tags", []),
            priority="medium",
            suggested_action=None,
            model_used=source_type,
            tokens_used=0,
            cost_usd=0,
            is_final=True,
        )
        if artifact_raw_text:
            try:
                await index_artifact(session, artifact.id)
            except Exception:
                pass

        if sensitive and raw_body:
            encrypted = encrypt_text(raw_body, associated_data=f"{source_type}:{external_id}:body")
            await store.upsert_protected_content(
                session,
                source_type=source_type,
                source_ref=external_id,
                content_kind="body",
                ciphertext=encrypted["ciphertext"],
                nonce=encrypted["nonce"],
                checksum=encrypted["checksum"],
                preview_text=redact_text(raw_body[:240]),
                metadata_={"title": title},
            )

        payload = {
            key: value
            for key, value in entry.items()
            if key not in {"raw_body_markdown"}
        }
        source_item, created = await store.upsert_source_item(
            session,
            sync_source_id=sync_source.id,
            external_id=external_id,
            title=title,
            summary=summary,
            payload=payload,
            content_hash=content_hash,
            external_url=entry.get("external_url"),
            project_note_id=project_note.id if project_note else None,
            artifact_id=artifact.id,
            happened_at=happened_dt,
        )

        story_fields = await _extract_story_fields(
            session,
            source_type=source_type,
            title=title,
            body_markdown=safe_body,
            project_ref=project_note.title if project_note else entry.get("project_ref"),
            actor_name=device_name or source_name,
        )
        await publish_story_entry(
            session,
            actor_type=(
                "connector"
                if source_type
                in {
                    "gmail",
                    "drive",
                    "google_keep",
                    "apple_notes",
                    "github",
                    "youtube_history",
                    "google_search_history",
                    "ott_history",
                }
                else "agent"
            ),
            actor_name=device_name or source_name,
            subject_type=story_fields["subject_type"],
            subject_ref=story_fields["subject_ref"] or (project_note.title if project_note else entry.get("project_ref")),
            entry_type=entry.get("entry_type") or story_fields["entry_type"],
            title=story_fields["title"] or title,
            body_markdown=safe_body,
            project_ref=project_note.title if project_note else entry.get("project_ref"),
            summary=story_fields["summary"] or summary or safe_body[:280],
            decision=story_fields["decision"],
            rationale=story_fields["rationale"],
            constraint=story_fields["constraint"],
            outcome=story_fields["outcome"],
            impact=story_fields["impact"],
            open_question=story_fields["open_question"],
            evidence_refs=story_fields["evidence_refs"],
            tags=story_fields["tags"] or entry.get("tags", []),
            source_links=entry.get("source_links", []),
            source=source_type,
            category=entry.get("category", "note"),
            metadata_={
                **metadata,
                "sensitive": sensitive,
                "protected_source_ref": external_id if sensitive else None,
            },
            happened_at=happened_dt,
            artifact_id=artifact.id,
            source_item_id=source_item.id,
        )

        session_meta = metadata if isinstance(metadata, dict) else {}
        if entry.get("entry_type") in {"conversation_session", "session_closeout"}:
            await store.upsert_conversation_session(
                session,
                source_item_id=source_item.id,
                project_note_id=project_note.id if project_note else None,
                agent_kind=str(session_meta.get("agent_kind") or source_type),
                session_id=str(session_meta.get("session_id") or external_id),
                parent_session_id=session_meta.get("parent_session_id"),
                cwd=session_meta.get("cwd"),
                title_hint=session_meta.get("title_hint") or title[:240],
                transcript_blob_ref=None,
                transcript_excerpt=(summary or safe_body)[:4000] or None,
                participants=list(session_meta.get("participants") or []),
                turn_count=int(session_meta.get("turn_count") or 0),
                started_at=_parse_optional_datetime(session_meta.get("started_at")),
                ended_at=_parse_optional_datetime(session_meta.get("ended_at")) or happened_dt,
                metadata_=session_meta,
            )

        if created:
            imported += 1

    await store.finish_sync_run(
        session,
        sync_run.id,
        status="completed",
        items_seen=len(entries),
        items_imported=imported,
    )
    await store.touch_sync_source(session, sync_source.id)

    result = {
        "status": "completed",
        "sync_source_id": str(sync_source.id),
        "sync_run_id": str(sync_run.id),
        "items_seen": len(entries),
        "items_imported": imported,
        "source_name": source_name,
        "source_type": source_type,
        "device_name": device_name,
        "mode": mode,
        "projects_touched": sorted(projects_touched),
    }
    if project_note_ids_touched:
        await recompute_project_states(
            session,
            project_note_ids=[uuid.UUID(value) for value in sorted(project_note_ids_touched)],
        )
    if emit_sync_event:
        await _publish_sync_event(result)
    if imported and emit_sync_event:
        await _trigger_story_pulse(reason=f"{source_type}:{mode}", metadata=result)
    return result
