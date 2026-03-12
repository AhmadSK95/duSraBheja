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


BOOTSTRAP_LOW_SIGNAL_ENTRY_TYPES = {"context_dump", "repo_snapshot", "knowledge_refresh", "voice_refresh"}
BOOTSTRAP_PRIORITY = {
    "session_closeout": 5,
    "conversation_session": 4,
    "progress_update": 4,
    "decision": 4,
    "research_thread": 3,
    "blind_spot": 3,
    "synapse": 2,
}


def _bootstrap_activity_rank(item: dict) -> tuple[int, str]:
    entry_type = str(item.get("entry_type") or "")
    happened_at = str(item.get("happened_at") or "")
    return (BOOTSTRAP_PRIORITY.get(entry_type, 1), happened_at)


def _relevant_bootstrap_activity(project_payload: dict | None) -> list[dict]:
    recent_activity = list((project_payload or {}).get("recent_activity") or [])
    filtered = [
        item
        for item in recent_activity
        if str(item.get("entry_type") or "") not in BOOTSTRAP_LOW_SIGNAL_ENTRY_TYPES
    ]
    filtered.sort(key=_bootstrap_activity_rank, reverse=True)
    return filtered


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


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

    snapshot = (project_payload or {}).get("snapshot") or {}
    relevant_activity = _relevant_bootstrap_activity(project_payload)
    recent_titles = _dedupe_strings([str(item.get("title") or "").strip() for item in relevant_activity if item.get("title")])
    open_loops = _dedupe_strings([
        str(item.get("open_question") or "").strip()
        for item in relevant_activity
        if item.get("open_question")
    ])
    blockers = list(snapshot.get("blockers") or [])
    if not blockers:
        blockers = [
            str(item.get("constraint") or "").strip()
            for item in relevant_activity
            if item.get("constraint")
        ][:6]
    where_it_stands = (
        snapshot.get("implemented")
        or ((project_payload or {}).get("project") or {}).get("content")
        or (recent_titles[0] if recent_titles else None)
    )
    what_changed = " | ".join(recent_titles[:2]) if recent_titles else snapshot.get("what_changed")
    what_is_left = (open_loops[0] if open_loops else None) or snapshot.get("remaining")

    return {
        "agent_kind": agent_kind,
        "session_id": session_id,
        "project": project_payload["project"] if project_payload else None,
        "reboot_brief": {
            "where_it_stands": where_it_stands,
            "what_changed": what_changed,
            "what_is_left": what_is_left,
            "blockers": blockers[:8],
            "open_loops": open_loops[:6] or recent_titles[:6],
            "related_repos": [item["name"] for item in ((project_payload or {}).get("repos") or [])[:5] if item.get("name")],
        },
        "recent_sessions": ((project_payload or {}).get("conversation_sessions") or [])[:5],
        "recent_activity": relevant_activity[:6],
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
        "decision": decisions[0] if decisions else None,
        "outcome": changes[0] if changes else None,
        "open_question": open_questions[0] if open_questions else None,
        "impact": "A fresh owned-agent session closeout was published into the shared brain.",
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
