"""Story-centric service helpers used by API, MCP, worker, and collector."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import normalize_category, normalize_tags
from src.lib import store
from src.lib.time import coerce_datetime, format_display_datetime, human_datetime_payload


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _story_entry_time(entry) -> datetime:
    return coerce_datetime(getattr(entry, "happened_at", None)) or coerce_datetime(getattr(entry, "created_at", None)) or _utcnow()


def _serialize_story_entry(entry) -> dict:
    payload = {
        "id": str(entry.id),
        "entry_type": entry.entry_type,
        "actor_type": entry.actor_type,
        "actor_name": entry.actor_name,
        "subject_type": entry.subject_type,
        "subject_ref": entry.subject_ref,
        "title": entry.title,
        "summary": entry.summary,
        "decision": entry.decision,
        "rationale": entry.rationale,
        "constraint": entry.constraint,
        "outcome": entry.outcome,
        "impact": entry.impact,
        "open_question": entry.open_question,
        "evidence_refs": entry.evidence_refs or [],
        "tags": list(entry.tags or []),
        "source_links": entry.source_links or [],
    }
    payload.update(human_datetime_payload(getattr(entry, "happened_at", None), prefix="happened_at"))
    return payload


def _serialize_reminder(item) -> dict:
    payload = {
        "id": str(item.id),
        "title": item.title,
        "recurrence_kind": item.recurrence_kind,
        "status": item.status,
    }
    payload.update(human_datetime_payload(getattr(item, "next_fire_at", None), prefix="next_fire_at", fallback="unscheduled"))
    return payload


async def publish_story_entry(
    session: AsyncSession,
    *,
    actor_type: str,
    actor_name: str,
    subject_type: str = "topic",
    subject_ref: str | None = None,
    entry_type: str,
    title: str,
    body_markdown: str,
    project_ref: str | None = None,
    summary: str | None = None,
    decision: str | None = None,
    rationale: str | None = None,
    constraint: str | None = None,
    outcome: str | None = None,
    impact: str | None = None,
    open_question: str | None = None,
    evidence_refs: list[str] | None = None,
    tags: list[str] | None = None,
    source_links: list[str] | None = None,
    source: str = "manual",
    category: str = "note",
    metadata_: dict | None = None,
    happened_at: datetime | None = None,
    artifact_id: uuid.UUID | None = None,
    source_item_id: uuid.UUID | None = None,
) -> dict:
    project_note = None
    if project_ref:
        project_note = await store.get_or_create_project_note(session, project_ref)

    if artifact_id is None:
        artifact = await store.create_artifact(
            session,
            content_type="text",
            raw_text=body_markdown,
            summary=title,
            source=source,
            metadata_={
                "actor_type": actor_type,
                "actor_name": actor_name,
                "entry_type": entry_type,
                **(metadata_ or {}),
            },
        )
        artifact_id = artifact.id

        await store.create_classification(
            session,
            artifact_id=artifact.id,
            category=normalize_category(category),
            confidence=1.0,
            entities=[],
            tags=tags or [],
            priority="medium",
            suggested_action=None,
            model_used="story-publish",
            tokens_used=0,
            cost_usd=0,
            is_final=True,
        )
    else:
        artifact = await store.get_artifact(session, artifact_id)

    journal_entry = await store.create_journal_entry(
        session,
        artifact_id=artifact_id,
        project_note_id=project_note.id if project_note else None,
        source_item_id=source_item_id,
        subject_type=subject_type,
        subject_ref=subject_ref or project_ref,
        entry_type=entry_type,
        actor_type=actor_type,
        actor_name=actor_name,
        title=title,
        body_markdown=body_markdown,
        summary=summary or (body_markdown or title)[:280],
        decision=decision,
        rationale=rationale,
        constraint=constraint,
        outcome=outcome,
        impact=impact,
        open_question=open_question,
        evidence_refs=evidence_refs or [],
        tags=normalize_tags(tags),
        source_links=source_links or [],
        metadata_=metadata_ or {},
        happened_at=happened_at or _utcnow(),
    )

    if project_note and artifact:
        await store.create_link(
            session,
            source_type="artifact",
            source_id=artifact.id,
            target_type="note",
            target_id=project_note.id,
            relation="related_to_project",
        )

    return {
        "artifact": artifact,
        "project_note": project_note,
        "journal_entry": journal_entry,
    }


async def build_project_story_payload(session: AsyncSession, project_note_id: uuid.UUID) -> dict | None:
    story = await store.get_project_story(session, project_note_id)
    if not story:
        return None
    snapshot = await store.get_project_state_snapshot(session, project_note_id)
    connections = await store.list_story_connections(session, project_note_id=project_note_id, limit=10)
    sessions = await store.list_conversation_sessions(session, project_note_id=project_note_id, limit=10)
    reminders = await store.list_project_reminders(session, project_note_id=project_note_id, status="active", limit=10)
    aliases = await store.list_project_aliases(session, project_note_id=project_note_id, limit=25)

    project = story["project"]
    serialized_activity = [_serialize_story_entry(entry) for entry in sorted(story["journal_entries"], key=_story_entry_time, reverse=True)]
    latest_activity = serialized_activity[0] if serialized_activity else None
    latest_closeout = next((item for item in serialized_activity if item.get("entry_type") == "session_closeout"), None)
    latest_closeout_time = coerce_datetime((latest_closeout or {}).get("happened_at_utc"))
    newer_activity_since_closeout = [
        item
        for item in serialized_activity
        if latest_closeout_time
        and (coerce_datetime(item.get("happened_at_utc")) or _utcnow()) > latest_closeout_time
        and item.get("entry_type") != "session_closeout"
    ]

    return {
        "project": {
            "id": str(project.id),
            "title": project.title,
            "category": project.category,
            "content": project.content,
            "status": project.status,
            "tags": list(project.tags or []),
            **human_datetime_payload(project.updated_at, prefix="updated_at"),
        },
        "snapshot": (
            {
                "active_score": snapshot.active_score,
                "status": snapshot.status,
                "manual_state": snapshot.manual_state,
                "confidence": snapshot.confidence,
                "implemented": snapshot.implemented,
                "remaining": snapshot.remaining,
                "blockers": snapshot.blockers or [],
                "risks": snapshot.risks or [],
                "holes": snapshot.holes or [],
                "what_changed": snapshot.what_changed,
                "why_active": snapshot.why_active,
                "why_not_active": snapshot.why_not_active,
                "last_signal_at": str(snapshot.last_signal_at) if snapshot.last_signal_at else None,
                "last_signal_at_display": format_display_datetime(snapshot.last_signal_at),
                "feature_scores": snapshot.feature_scores or {},
            }
            if snapshot
            else None
        ),
        "repos": [
            {
                "id": str(repo.id),
                "owner": repo.repo_owner,
                "name": repo.repo_name,
                "url": repo.repo_url,
                "branch": repo.branch,
                "local_path": repo.local_path,
                "is_primary": repo.is_primary,
            }
            for repo in story["repos"]
        ],
        "aliases": [
            {
                "id": str(alias.id),
                "alias": alias.alias,
                "normalized_alias": alias.normalized_alias,
                "source_type": alias.source_type,
                "source_ref": alias.source_ref,
                "confidence": alias.confidence,
                "is_manual": alias.is_manual,
            }
            for alias in aliases
        ],
        "recent_activity": [
            item
            for item in serialized_activity
        ],
        "sources": [
            {
                "id": str(item.id),
                "external_id": item.external_id,
                "title": item.title,
                "summary": item.summary,
                "external_url": item.external_url,
                **human_datetime_payload(item.happened_at, prefix="happened_at"),
            }
            for item in story["source_items"]
        ],
        "links": [
            {
                "relation": link.relation,
                "target_type": link.target_type,
                "target_id": str(link.target_id),
            }
            for link in story["related_links"]
        ],
        "connections": [
            {
                "source_ref": item.source_ref,
                "target_ref": item.target_ref,
                "relation": item.relation,
                "weight": item.weight,
                "evidence_count": item.evidence_count,
            }
            for item in connections
        ],
        "conversation_sessions": [
            {
                "id": str(item.id),
                "agent_kind": item.agent_kind,
                "session_id": item.session_id,
                "parent_session_id": item.parent_session_id,
                "cwd": item.cwd,
                "title_hint": item.title_hint,
                "turn_count": item.turn_count,
                **human_datetime_payload(item.started_at, prefix="started_at"),
                **human_datetime_payload(item.ended_at, prefix="ended_at"),
            }
            for item in sessions
        ],
        "reminders": [
            _serialize_reminder(item)
            for item in reminders
        ],
        "latest_activity": latest_activity,
        "latest_closeout": latest_closeout,
        "newer_activity_since_closeout": newer_activity_since_closeout[:10],
        "closeout_is_latest_activity": bool(latest_closeout and latest_activity and latest_closeout["id"] == latest_activity["id"]),
    }


async def build_project_brief_payload(session: AsyncSession, project_note_id: uuid.UUID) -> dict | None:
    payload = await build_project_story_payload(session, project_note_id)
    if not payload:
        return None

    recent_titles = [entry["title"] for entry in payload["recent_activity"][:5]]
    repo_names = [repo["name"] for repo in payload["repos"]]
    source_titles = [item["title"] for item in payload["sources"][:5]]

    return {
        "project_id": payload["project"]["id"],
        "title": payload["project"]["title"],
        "status": (payload.get("snapshot") or {}).get("status") or payload["project"]["status"],
        "summary": payload["project"]["content"] or payload["project"]["title"],
        "latest_updates": recent_titles,
        "repo_names": repo_names,
        "recent_sources": source_titles,
        "tags": payload["project"]["tags"],
        "active_score": (payload.get("snapshot") or {}).get("active_score"),
        "implemented": (payload.get("snapshot") or {}).get("implemented"),
        "remaining": (payload.get("snapshot") or {}).get("remaining"),
        "holes": (payload.get("snapshot") or {}).get("holes") or [],
        "latest_closeout": payload.get("latest_closeout"),
    }


async def build_project_latest_closeout_payload(session: AsyncSession, project_note_id: uuid.UUID) -> dict | None:
    payload = await build_project_story_payload(session, project_note_id)
    if not payload:
        return None
    return {
        "project": payload.get("project"),
        "latest_activity": payload.get("latest_activity"),
        "latest_closeout": payload.get("latest_closeout"),
        "newer_activity_since_closeout": payload.get("newer_activity_since_closeout") or [],
        "closeout_is_latest_activity": payload.get("closeout_is_latest_activity"),
    }
