"""Librarian task — create or merge canonical notes."""

import logging
import uuid

from src.agents.librarian import process_artifact
from src.constants import MERGEABLE_CATEGORIES, normalize_category
from src.database import async_session
from src.lib.store import (
    create_journal_entry,
    create_link,
    create_note,
    find_notes_by_title,
    get_artifact,
    get_note,
    get_related,
    update_note,
)
from src.lib.time import human_datetime_text
from src.models import Classification
from src.services.library import promote_single_artifact
from src.services.planner import build_planner_payload
from src.services.reminders import store_reminder
from src.worker.main import EVENT_ARTIFACT_PROCESSED, publish_event

log = logging.getLogger("brain-worker.librarian")

PLANNER_CATEGORIES = {"daily_planner", "weekly_planner"}


async def process_librarian(ctx, artifact_id: str, classification_id: str):
    """Create or update a canonical note based on the classified artifact."""
    artifact_uuid = uuid.UUID(artifact_id)
    classification_uuid = uuid.UUID(classification_id)

    async with async_session() as session:
        artifact = await get_artifact(session, artifact_uuid)
        if not artifact:
            log.error(f"Artifact {artifact_id} not found")
            return

        classification = await session.get(Classification, classification_uuid)
        if not classification:
            log.error(f"Classification {classification_id} not found")
            return

        try:
            classification_data = {
                "category": normalize_category(classification.category),
                "confidence": classification.confidence,
                "entities": classification.entities or [],
                "tags": list(classification.tags or []),
                "summary": artifact.summary or "",
            }

            artifact_note = await _get_existing_artifact_note(session, artifact_uuid)
            planner_payload = None

            if classification.category in PLANNER_CATEGORIES:
                planner_payload = build_planner_payload(
                    artifact.raw_text,
                    classification_data,
                    fallback_summary=artifact.summary or "",
                )
                note_id, note_title, note_content = await _upsert_planner_note(
                    session,
                    artifact_note,
                    classification,
                    planner_payload,
                )
                reminder = None
            elif classification.category == "reminder":
                note_id, note_title, note_content, reminder = await _upsert_reminder_note(
                    session,
                    artifact,
                    artifact_note,
                    classification,
                )
            else:
                note_id, note_title, note_content = await _upsert_canonical_note(
                    session,
                    classification,
                    classification_data,
                    artifact.raw_text,
                    artifact_note,
                )
                reminder = None

            if artifact_note is None:
                await create_link(
                    session,
                    source_type="artifact",
                    source_id=artifact_uuid,
                    target_type="note",
                    target_id=note_id,
                    relation="derived_from",
                )

            # C3: Promote artifact to EvidenceRecord inline
            try:
                await promote_single_artifact(session, artifact)
            except Exception:
                log.warning(
                    "Inline evidence promotion failed for artifact %s (non-fatal)", artifact_id
                )

            capture_intent = getattr(classification, "capture_intent", "thought")
            reply_target_kind = (artifact.metadata_ or {}).get("reply_target_kind")
            project_note_id = note_id if classification.category == "project" else None
            await create_journal_entry(
                session,
                artifact_id=artifact_uuid,
                project_note_id=project_note_id,
                entry_type=(
                    "feedback"
                    if reply_target_kind and capture_intent in {"critique", "question"}
                    else "artifact_ingested"
                ),
                actor_type="human"
                if artifact.source in {"discord", "manual", "command"}
                else "agent",
                actor_name=artifact.source,
                title=artifact.summary or note_title,
                body_markdown=artifact.raw_text,
                summary=artifact.summary or note_title,
                tags=list(classification.tags or []),
                source_links=[],
                metadata_={
                    "category": classification.category,
                    "note_id": str(note_id),
                    "capture_intent": capture_intent,
                    "reply_target_kind": reply_target_kind,
                    "reply_target_message_id": (artifact.metadata_ or {}).get(
                        "reply_target_message_id"
                    ),
                },
            )

            await publish_event(
                EVENT_ARTIFACT_PROCESSED,
                {
                    "artifact_id": str(artifact_uuid),
                    "discord_message_id": artifact.discord_message_id,
                    "discord_channel_id": artifact.discord_channel_id,
                    "source": artifact.source,
                    "summary": artifact.summary or note_title,
                    "category": classification.category,
                    "confidence": float(classification.confidence or 0),
                    "tags": list(classification.tags or []),
                    "note_id": str(note_id),
                    "note_title": note_title,
                    "note_content_preview": (note_content or "")[:1200],
                    "capture_intent": capture_intent,
                    "validation_status": getattr(classification, "validation_status", "validated"),
                    "reminder": (
                        {
                            "title": reminder.title,
                            "next_fire_at": human_datetime_text(
                                reminder.next_fire_at if reminder else None, fallback="unscheduled"
                            ),
                        }
                        if reminder
                        else None
                    ),
                },
            )
        except Exception as exc:
            from src.worker.main import EVENT_ARTIFACT_FAILED

            log.exception("Failed librarian processing for artifact %s", artifact_id)
            await publish_event(
                EVENT_ARTIFACT_FAILED,
                {
                    "artifact_id": str(artifact_uuid),
                    "discord_message_id": artifact.discord_message_id,
                    "discord_channel_id": artifact.discord_channel_id,
                    "stage": "librarian",
                    "error": str(exc),
                },
            )
            raise


async def _get_existing_artifact_note(session, artifact_uuid: uuid.UUID):
    links = await get_related(session, "artifact", artifact_uuid)
    for link in links:
        if link.target_type != "note" or link.relation != "derived_from":
            continue
        note = await get_note(session, link.target_id)
        if note:
            return note
    return None


async def _upsert_planner_note(session, artifact_note, classification, planner_payload: dict):
    if artifact_note:
        await update_note(
            session,
            artifact_note.id,
            category=classification.category,
            title=planner_payload["title"],
            content=planner_payload["content"],
            tags=planner_payload["tags"],
            priority=classification.priority or "medium",
            metadata_=planner_payload["metadata"],
        )
        note_id = artifact_note.id
        log.info("Updated planner note %s: %s", note_id, planner_payload["title"])
    else:
        note = await create_note(
            session,
            category=classification.category,
            title=planner_payload["title"],
            content=planner_payload["content"],
            tags=planner_payload["tags"],
            priority=classification.priority or "medium",
            metadata_=planner_payload["metadata"],
        )
        note_id = note.id
        log.info("Created planner note %s: %s", note_id, planner_payload["title"])

    return note_id, planner_payload["title"], planner_payload["content"]


async def _upsert_canonical_note(
    session, classification, classification_data: dict, artifact_text: str, artifact_note
):
    existing_note = artifact_note
    existing_note_content = artifact_note.content if artifact_note else None

    if existing_note is None and classification.category in MERGEABLE_CATEGORIES:
        entity_names = [
            e["value"]
            for e in (classification.entities or [])
            if e.get("type") in ("person", "project")
        ]

        for name in entity_names:
            matches = await find_notes_by_title(session, name, classification.category)
            if matches:
                existing_note = matches[0]
                existing_note_content = existing_note.content
                break

    result = await process_artifact(
        session,
        artifact_text=artifact_text,
        classification=classification_data,
        existing_note_content=existing_note_content,
    )

    if existing_note:
        await update_note(
            session,
            existing_note.id,
            content=result["content"],
            title=result["title"],
            tags=result.get("tags", list(classification.tags or [])),
            priority=classification.priority or "medium",
        )
        note_id = existing_note.id
        log.info("Updated note %s: %s", note_id, result["title"])
    else:
        note = await create_note(
            session,
            category=classification.category,
            title=result["title"],
            content=result["content"],
            tags=result.get("tags", list(classification.tags or [])),
            priority=classification.priority or "medium",
        )
        note_id = note.id
        log.info("Created note %s: %s", note_id, result["title"])

    return note_id, result["title"], result["content"]


async def _upsert_reminder_note(session, artifact, artifact_note, classification):
    title = artifact.summary or artifact.raw_text[:120]
    project_note_id = None
    for entity in classification.entities or []:
        if entity.get("type") != "project":
            continue
        matches = await find_notes_by_title(session, entity.get("value"), "project")
        if matches:
            project_note_id = matches[0].id
            break

    if artifact_note:
        await update_note(
            session,
            artifact_note.id,
            category="reminder",
            title=title,
            content=artifact.raw_text,
            tags=list(classification.tags or []),
            priority=classification.priority or "medium",
            discord_channel_id=artifact.discord_channel_id,
        )
        note = artifact_note
    else:
        note = await create_note(
            session,
            category="reminder",
            title=title,
            content=artifact.raw_text,
            tags=list(classification.tags or []),
            priority=classification.priority or "medium",
            discord_channel_id=artifact.discord_channel_id,
        )

    reminder = await store_reminder(
        session,
        raw_text=artifact.raw_text,
        note_id=note.id,
        project_note_id=project_note_id,
        discord_channel_id=artifact.discord_channel_id,
    )
    metadata = dict(note.metadata_ or {})
    metadata["reminder_id"] = str(reminder.id)
    metadata["next_fire_at"] = human_datetime_text(reminder.next_fire_at, fallback="unscheduled")
    metadata["next_fire_at_utc"] = (
        reminder.next_fire_at.isoformat() if reminder.next_fire_at else None
    )
    await update_note(session, note.id, metadata_=metadata, remind_at=reminder.next_fire_at)
    return note.id, note.title, note.content, reminder
