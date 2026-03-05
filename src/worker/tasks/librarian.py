"""Librarian task — create or merge canonical notes."""

import json
import logging
import uuid

from src.database import async_session
from src.agents.librarian import process_artifact
from src.lib.store import (
    get_artifact,
    get_note,
    create_note,
    update_note,
    find_notes_by_title,
    create_link,
)
from src.models import Classification

log = logging.getLogger("brain-worker.librarian")

# Categories that get canonical note merging
MERGEABLE_CATEGORIES = {"people", "project"}


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

        classification_data = {
            "category": classification.category,
            "confidence": classification.confidence,
            "entities": classification.entities or [],
            "tags": list(classification.tags or []),
            "summary": artifact.summary or "",
        }

        # For People and Projects, try to find existing note to merge into
        existing_note = None
        existing_note_content = None

        if classification.category in MERGEABLE_CATEGORIES:
            # Search for existing note by entity names
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

        # Call librarian agent
        result = await process_artifact(
            session,
            artifact_text=artifact.raw_text,
            classification=classification_data,
            existing_note_content=existing_note_content,
        )

        # Create or update note
        if result["action"] == "update" and existing_note:
            await update_note(
                session,
                existing_note.id,
                content=result["content"],
                tags=result.get("tags", list(classification.tags or [])),
            )
            note_id = existing_note.id
            log.info(f"Updated note {note_id}: {existing_note.title}")
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
            log.info(f"Created note {note_id}: {result['title']}")

        # Link artifact → note
        await create_link(
            session,
            source_type="artifact",
            source_id=artifact_uuid,
            target_type="note",
            target_id=note_id,
            relation="derived_from",
        )
