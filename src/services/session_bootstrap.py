"""Owned-agent reboot and closeout contract."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import store
from src.services.identity import resolve_project
from src.services.openai_web import research_topic_brief
from src.services.persona import build_persona_packet
from src.services.profile_narrative import materialize_profile_read_models
from src.services.project_state import recompute_project_states
from src.services.query import collect_sources
from src.services.source_ingest import ingest_source_entries
from src.services.story import build_project_story_payload, publish_story_entry


BOOTSTRAP_LOW_SIGNAL_ENTRY_TYPES = {
    "context_dump",
    "context_signal_dump",
    "directory_inventory",
    "repo_snapshot",
    "repo_signal_summary",
    "workspace_signal_summary",
    "workspace_landscape_summary",
    "agent_memory_snapshot",
    "plan_snapshot",
    "todo_snapshot",
    "agent_reference_signal",
    "agent_plan_signal",
    "agent_todo_signal",
    "knowledge_refresh",
    "voice_refresh",
}
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


def _detect_self_bootstrap_mode(task_hint: str | None, cwd: str | None) -> str:
    lowered = " ".join(filter(None, [task_hint or "", cwd or ""])).lower()
    if any(token in lowered for token in ("iit", "kharagpur", "nyu", "amazon", "citicorp", "loylty", "institution")):
        return "institution"
    if any(token in lowered for token in ("expertise", "skill", "strength", "engineer", "know")):
        return "expertise"
    if any(token in lowered for token in ("project", "built", "case study", "portfolio")):
        return "project_cases"
    if any(token in lowered for token in ("coverage", "missing", "ingest", "history")):
        return "coverage"
    return "identity"


def _self_bootstrap_from_models(profile_models: dict[str, dict], *, mode: str) -> tuple[dict, list[dict], list[str]]:
    overview = dict(profile_models.get("profile:overview") or {})
    timeline = dict(profile_models.get("profile:timeline") or {})
    expertise = dict(profile_models.get("profile:expertise") or {})
    projects = dict(profile_models.get("profile:projects") or {})
    coverage = dict(profile_models.get("profile:coverage") or {})
    institutions = dict(profile_models.get("profile:institutions") or {})
    identity = dict(profile_models.get("profile:identity") or {})

    focus = list((overview.get("current_arc") or {}).get("focus") or [])
    coverage_gaps = list(coverage.get("gaps") or [])
    institution_items = list(institutions.get("items") or [])
    expertise_books = list(expertise.get("books") or [])
    project_items = list(projects.get("items") or [])
    era_items = list(timeline.get("eras") or [])

    sources: list[dict] = []
    open_loops = focus[:4]
    blockers = [item.get("title") or "" for item in coverage_gaps[:4] if item.get("title")]
    related_refs: list[str] = []

    if mode == "institution":
        sources = institution_items[:5] or era_items[:5]
        open_loops = [item.get("summary") or "" for item in institution_items[:3] if item.get("summary")] or open_loops
        related_refs = [item.get("title") or "" for item in institution_items[:5] if item.get("title")]
    elif mode == "expertise":
        sources = expertise_books[:6]
        open_loops = [item.get("summary") or "" for item in expertise_books[:3] if item.get("summary")] or open_loops
        related_refs = [item.get("title") or "" for item in expertise_books[:6] if item.get("title")]
    elif mode == "project_cases":
        sources = project_items[:6]
        related_refs = [item.get("title") or "" for item in project_items[:6] if item.get("title")]
    elif mode == "coverage":
        sources = coverage_gaps[:6]
        open_loops = [item.get("recommendation") or "" for item in coverage_gaps[:4] if item.get("recommendation")] or open_loops
        related_refs = [item.get("title") or "" for item in coverage_gaps[:6] if item.get("title")]
    else:
        sources = [
            {"title": "Identity", "summary": item}
            for item in list(identity.get("identity_stack") or overview.get("identity_stack") or [])[:5]
        ]
        related_refs = [item.get("title") or "" for item in project_items[:4] if item.get("title")]

    reboot_brief = {
        "where_it_stands": (overview.get("current_arc") or {}).get("summary") or overview.get("summary"),
        "what_changed": "Private self-knowledge is now materialized through profile read models instead of only project reboots.",
        "what_is_left": (coverage_gaps[0] or {}).get("recommendation") if coverage_gaps else None,
        "blockers": blockers[:8],
        "open_loops": _dedupe_strings([item for item in open_loops if item])[:6],
        "related_repos": related_refs[:5],
    }
    return reboot_brief, sources[:6], related_refs[:5]


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
    persona_packet = await build_persona_packet(session)
    profile_models = await materialize_profile_read_models(session) if not project_payload else {}
    self_bootstrap_mode = _detect_self_bootstrap_mode(task_hint, cwd) if not project_payload else "project"

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

    if not project_payload:
        reboot_brief, curated_sources, related_refs = _self_bootstrap_from_models(profile_models, mode=self_bootstrap_mode)
        if curated_sources:
            brain_sources = [
                {
                    "title": item.get("title") or item.get("slug") or f"profile:{index}",
                    "summary": item.get("summary") or item.get("tagline") or "",
                    "category": "profile",
                }
                for index, item in enumerate(curated_sources, start=1)
            ]
        where_it_stands = reboot_brief.get("where_it_stands")
        what_changed = reboot_brief.get("what_changed")
        what_is_left = reboot_brief.get("what_is_left")
        blockers = reboot_brief.get("blockers") or blockers
        open_loops = reboot_brief.get("open_loops") or open_loops
        recent_titles = related_refs or recent_titles

    return {
        "agent_kind": agent_kind,
        "session_id": session_id,
        "bootstrap_mode": self_bootstrap_mode,
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
        "profile_models": {
            key: profile_models.get(key)
            for key in (
                "profile:overview",
                "profile:identity",
                "profile:timeline",
                "profile:institutions",
                "profile:expertise",
                "profile:projects",
                "profile:coverage",
            )
            if key in profile_models
        },
        "voice_profile": (
            {
                "summary": voice_profile.summary,
                "traits": voice_profile.traits,
                "style_anchors": voice_profile.style_anchors[:5],
            }
            if voice_profile
            else None
        ),
        "persona_packet": persona_packet,
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


async def publish_curated_session_story(
    session: AsyncSession,
    *,
    agent_kind: str,
    session_id: str,
    project_ref: str,
    title: str,
    summary: str,
    direction: str | None = None,
    changes: list[str] | None = None,
    open_loops: list[str] | None = None,
    source_links: list[str] | None = None,
    transcript_excerpt: str | None = None,
    tags: list[str] | None = None,
    actor_name: str | None = None,
) -> dict:
    body_lines = [
        f"# {title}",
        "",
        "## Summary",
        summary.strip(),
    ]
    if direction:
        body_lines.extend(["", "## Direction", direction.strip()])
    if changes:
        body_lines.extend(["", "## What Changed", *[f"- {item}" for item in changes if item]])
    if open_loops:
        body_lines.extend(["", "## Open Loops", *[f"- {item}" for item in open_loops if item]])
    if transcript_excerpt:
        body_lines.extend(["", "## Session Excerpt", transcript_excerpt[:2000]])

    result = await publish_story_entry(
        session,
        actor_type="agent",
        actor_name=actor_name or agent_kind,
        subject_type="project",
        subject_ref=project_ref,
        entry_type="progress_update",
        title=title,
        body_markdown="\n".join(body_lines).strip(),
        project_ref=project_ref,
        summary=summary[:280],
        outcome=changes[0] if changes else None,
        impact=direction[:280] if direction else None,
        open_question=open_loops[0] if open_loops else None,
        tags=["curated-session-story", agent_kind, *(tags or [])],
        source_links=source_links or [],
        source="agent",
        category="project",
        metadata_={
            "agent_kind": agent_kind,
            "session_id": session_id,
            "story_kind": "curated_session_story",
            "changes": changes or [],
            "open_loops": open_loops or [],
        },
    )

    project = await resolve_project(
        session,
        project_hint=project_ref,
        source_refs=[title, summary],
        create_if_missing=False,
    )
    if project:
        await recompute_project_states(session, project_note_ids=[project.id])
        project_payload = await build_project_story_payload(session, project.id)
    else:
        project_payload = None
    return {
        "status": "stored",
        "journal_entry_id": str(result["journal_entry"].id),
        "project": project_payload["project"] if project_payload else None,
        "entry_type": "progress_update",
    }
