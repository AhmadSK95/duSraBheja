"""Owned-agent reboot and closeout contract."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import store
from src.services.identity import resolve_project
from src.services.openai_web import research_topic_brief
from src.services.project_state import recompute_project_states
from src.services.query import collect_sources
from src.services.source_ingest import ingest_source_entries
from src.services.story import build_project_story_payload


async def build_session_bootstrap(
    session: AsyncSession,
    *,
    agent_kind: str,
    session_id: str,
    cwd: str | None = None,
    project_hint: str | None = None,
    task_hint: str | None = None,
    include_web: bool = True,
) -> dict:
    project = await resolve_project(
        session,
        project_hint=project_hint or task_hint,
        cwd=cwd,
        source_refs=[task_hint],
        create_if_missing=False,
    )
    if project:
        await recompute_project_states(session, project_note_ids=[project.id])
        project_payload = await build_project_story_payload(session, project.id)
    else:
        project_payload = None

    subject = project_payload["project"]["title"] if project_payload else (project_hint or cwd or task_hint or "current work")
    brain_sources = await collect_sources(session, subject, category="project" if project_payload else None, limit=6)
    voice_profile = await store.get_voice_profile(session, "ahmad-default")

    web_brief = None
    if include_web and subject:
        questions = []
        snapshot = (project_payload or {}).get("snapshot") or {}
        for item in (snapshot.get("remaining"), *(snapshot.get("holes") or []), task_hint):
            if item:
                questions.append(str(item))
        web_brief = await research_topic_brief(topic=subject, questions=questions[:4])

    return {
        "agent_kind": agent_kind,
        "session_id": session_id,
        "project": project_payload["project"] if project_payload else None,
        "reboot_brief": {
            "where_it_stands": ((project_payload or {}).get("snapshot") or {}).get("implemented"),
            "what_changed": ((project_payload or {}).get("snapshot") or {}).get("what_changed"),
            "what_is_left": ((project_payload or {}).get("snapshot") or {}).get("remaining"),
            "blockers": ((project_payload or {}).get("snapshot") or {}).get("blockers") or [],
            "open_loops": [
                item.get("open_question") or item.get("title")
                for item in ((project_payload or {}).get("recent_activity") or [])[:6]
                if item.get("open_question") or item.get("title")
            ],
            "related_repos": [item["name"] for item in ((project_payload or {}).get("repos") or [])[:5] if item.get("name")],
        },
        "recent_sessions": ((project_payload or {}).get("conversation_sessions") or [])[:5],
        "reminders": ((project_payload or {}).get("reminders") or [])[:8],
        "connections": ((project_payload or {}).get("connections") or [])[:8],
        "brain_sources": brain_sources,
        "web_sources": list((web_brief or {}).get("findings") or [])[:5] if web_brief else [],
        "voice_profile": (
            {
                "summary": voice_profile.summary,
                "traits": voice_profile.traits,
                "style_anchors": voice_profile.style_anchors[:5],
            }
            if voice_profile
            else None
        ),
    }


async def record_session_closeout(
    session: AsyncSession,
    *,
    agent_kind: str,
    session_id: str,
    cwd: str | None = None,
    project_ref: str | None = None,
    summary: str,
    decisions: list[str],
    changes: list[str],
    open_questions: list[str],
    source_links: list[str],
    transcript_excerpt: str | None = None,
) -> dict:
    body_lines = [
        f"# {agent_kind.title()} session closeout",
        f"Session ID: {session_id}",
    ]
    if cwd:
        body_lines.append(f"CWD: {cwd}")
    if project_ref:
        body_lines.append(f"Project: {project_ref}")
    body_lines.extend(
        [
            "",
            "## Summary",
            summary,
        ]
    )
    if decisions:
        body_lines.extend(["", "## Decisions", *[f"- {item}" for item in decisions]])
    if changes:
        body_lines.extend(["", "## Changes", *[f"- {item}" for item in changes]])
    if open_questions:
        body_lines.extend(["", "## Open Questions", *[f"- {item}" for item in open_questions]])
    if transcript_excerpt:
        body_lines.extend(["", "## Transcript Excerpt", transcript_excerpt[:2000]])

    entry = {
        "external_id": f"agent:session_closeout:{agent_kind}:{session_id}",
        "project_ref": project_ref,
        "title": f"{agent_kind.title()} closeout: {project_ref or session_id}",
        "summary": summary[:240],
        "category": "project" if project_ref else "note",
        "entry_type": "session_closeout",
        "body_markdown": "\n".join(body_lines).strip(),
        "tags": ["agent-closeout", agent_kind],
        "source_links": source_links,
        "metadata": {
            "agent_kind": agent_kind,
            "session_id": session_id,
            "cwd": cwd,
            "title_hint": summary[:240],
            "participants": ["assistant"],
            "turn_count": 0,
            "started_at": None,
            "ended_at": None,
            "decisions": decisions,
            "changes": changes,
            "open_questions": open_questions,
        },
        "content_hash": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{agent_kind}:{session_id}:{summary}")),
    }
    result = await ingest_source_entries(
        session,
        source_type="agent",
        source_name=agent_kind,
        mode="sync",
        device_name=agent_kind,
        entries=[entry],
    )
    project = await resolve_project(
        session,
        project_hint=project_ref,
        cwd=cwd,
        source_refs=[summary],
        create_if_missing=False,
    )
    project_payload = await build_project_story_payload(session, project.id) if project else None
    return {
        "status": "stored",
        "project": project_payload["project"] if project_payload else None,
        "sync": result,
    }
