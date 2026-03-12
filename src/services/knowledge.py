"""Asynchronous knowledge-base enrichment across the broader brain."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import store
from src.services.indexing import index_artifact
from src.services.openai_web import research_topic_brief
from src.services.story import publish_story_entry


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _knowledge_note_title(subject_name: str) -> str:
    return f"Knowledge Base: {subject_name}"


async def refresh_knowledge_base(session: AsyncSession, *, limit: int = 3) -> dict:
    snapshots = await store.list_project_state_snapshots(session, limit=30)
    ideas = await store.list_notes(session, category="idea", limit=30)
    people = await store.list_notes(session, category="people", limit=20)
    connections = await store.list_story_connections(session, limit=80)
    candidates: list[tuple[str, object | None, object | None, str]] = []
    seen_subjects: set[str] = set()

    def add_candidate(
        subject_name: str | None,
        *,
        source_note=None,
        snapshot=None,
        subject_type: str,
    ) -> None:
        name = (subject_name or "").strip()
        if not name:
            return
        lowered = name.lower()
        if lowered in seen_subjects:
            return
        seen_subjects.add(lowered)
        candidates.append((name, source_note, snapshot, subject_type))

    for snapshot in snapshots:
        project = await store.get_note(session, snapshot.project_note_id)
        if not project:
            continue
        add_candidate(project.title, source_note=project, snapshot=snapshot, subject_type="project")
    for idea in ideas:
        add_candidate(idea.title, source_note=idea, subject_type="idea")
    for person in people:
        add_candidate(person.title, source_note=person, subject_type="people")
    for connection in connections:
        add_candidate(connection.source_ref, subject_type="topic")
        add_candidate(connection.target_ref, subject_type="topic")

    def candidate_sort_key(item):
        subject_name, source_note, snapshot, _subject_type = item
        metadata = dict((source_note.metadata_ or {}) if source_note else {})
        last_knowledge = metadata.get("knowledge_last_refreshed_at")
        if last_knowledge:
            try:
                if str(last_knowledge).endswith("Z"):
                    last_knowledge = str(last_knowledge).replace("Z", "+00:00")
                refreshed = datetime.fromisoformat(str(last_knowledge))
            except ValueError:
                refreshed = datetime(1970, 1, 1, tzinfo=timezone.utc)
        else:
            refreshed = datetime(1970, 1, 1, tzinfo=timezone.utc)
        score = getattr(snapshot, "active_score", 0.0) if snapshot else 0.15
        updated_at = source_note.updated_at if source_note else datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (refreshed, -score, updated_at, subject_name.lower())

    candidates.sort(key=candidate_sort_key)
    processed = 0
    touched_projects: list[str] = []
    for subject_name, source_note, snapshot, subject_type in candidates:
        if processed >= limit:
            break
        holes = list(getattr(snapshot, "holes", []) or [])
        risks = list(getattr(snapshot, "risks", []) or [])
        questions = [
            item
            for item in [
                getattr(snapshot, "remaining", None),
                *holes[:2],
                *risks[:2],
                ((source_note.content or "")[:280] if source_note else None),
            ]
            if item
        ]
        brief = await research_topic_brief(topic=subject_name, questions=questions)
        if not brief:
            continue

        body_lines = [
            f"# Knowledge Base: {subject_name}",
            "",
            "## Summary",
            str(brief.get("summary") or "No summary returned."),
            "",
            "## Findings",
        ]
        for item in brief.get("findings") or []:
            body_lines.append(
                f"- {item.get('title')}: {item.get('detail')} (source hint: {item.get('source_hint') or 'n/a'})"
            )
        followups = [item for item in brief.get("followups") or [] if item]
        if followups:
            body_lines.extend(["", "## Follow-ups"])
            body_lines.extend(f"- {item}" for item in followups[:8])
        body_markdown = "\n".join(body_lines)

        artifact = await store.create_artifact(
            session,
            content_type="text",
            raw_text=body_markdown,
            summary=_knowledge_note_title(subject_name),
            source="knowledge",
            metadata_={"subject_ref": subject_name, "generated_at": _utcnow().isoformat()},
        )
        await store.create_classification(
            session,
            artifact_id=artifact.id,
            category="resource",
            confidence=0.95,
            entities=[],
            tags=["knowledge-base", "web-research", subject_name.lower()],
            priority="medium",
            suggested_action=None,
            model_used="openai-web-search",
            tokens_used=0,
            cost_usd=0,
            is_final=True,
        )
        try:
            await index_artifact(session, artifact.id)
        except Exception:
            pass

        matches = await store.find_notes_by_title(session, _knowledge_note_title(subject_name), "resource")
        if matches:
            knowledge_note = await store.update_note(
                session,
                matches[0].id,
                title=_knowledge_note_title(subject_name),
                content=body_markdown,
                tags=["knowledge-base", subject_name.lower().replace(" ", "-")],
            )
        else:
            knowledge_note = await store.create_note(
                session,
                category="resource",
                title=_knowledge_note_title(subject_name),
                content=body_markdown,
                tags=["knowledge-base", subject_name.lower().replace(" ", "-")],
                priority="medium",
            )
        if source_note:
            try:
                await store.create_link(
                    session,
                    source_type="note",
                    source_id=knowledge_note.id,
                    target_type="note",
                    target_id=source_note.id,
                    relation="supports_subject",
                )
            except Exception:
                pass
        await publish_story_entry(
            session,
            actor_type="connector",
            actor_name="knowledge-base",
            subject_type="project" if getattr(snapshot, "project_note_id", None) else subject_type,
            subject_ref=subject_name,
            entry_type="knowledge_refresh",
            title=_knowledge_note_title(subject_name),
            body_markdown=body_markdown,
            project_ref=subject_name if getattr(snapshot, "project_note_id", None) else None,
            summary=str(brief.get("summary") or "")[:280],
            impact="New external knowledge was attached to a brain subject.",
            open_question=(followups[0] if followups else None),
            tags=["knowledge-base", "web-research"],
            source="knowledge",
            category="resource",
            artifact_id=artifact.id,
        )
        processed += 1
        if source_note:
            source_metadata = dict(source_note.metadata_ or {})
            source_metadata["knowledge_last_refreshed_at"] = _utcnow().isoformat()
            await store.update_note(session, source_note.id, metadata_=source_metadata)
        touched_projects.append(subject_name)
    return {"status": "completed", "items_imported": processed, "projects_touched": touched_projects}
