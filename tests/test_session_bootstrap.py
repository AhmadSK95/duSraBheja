from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from src.services import session_bootstrap


def test_bootstrap_prefers_recent_agent_activity_over_knowledge_refresh(monkeypatch) -> None:
    project = SimpleNamespace(id="project-1", title="duSraBheja")
    project_payload = {
        "project": {
            "id": "project-1",
            "title": "duSraBheja",
            "content": "Second brain for Ahmad",
        },
        "snapshot": {
            "implemented": "Brain core exists",
            "remaining": "Verify the live digest",
            "blockers": [],
            "holes": [],
            "what_changed": "Old generic summary",
        },
        "recent_activity": [
            {
                "entry_type": "knowledge_refresh",
                "actor_type": "connector",
                "title": "Knowledge Base: barbershop",
                "happened_at": "2026-03-12T18:30:00+00:00",
            },
            {
                "entry_type": "session_closeout",
                "actor_type": "agent",
                "title": "Codex closeout: ranking repair",
                "open_question": "Does the fresh digest now focus on current work?",
                "happened_at": "2026-03-12T18:10:00+00:00",
            },
            {
                "entry_type": "progress_update",
                "actor_type": "agent",
                "title": "Startup replay now rebuilds planner cards",
                "happened_at": "2026-03-12T18:00:00+00:00",
            },
        ],
        "conversation_sessions": [],
        "reminders": [],
        "connections": [],
        "repos": [{"name": "duSraBheja"}],
    }

    async def fake_resolve_project(session, **kwargs):
        return project

    async def fake_recompute_project_states(session, **kwargs):
        return []

    async def fake_build_project_story_payload(session, project_note_id):
        assert project_note_id == "project-1"
        return project_payload

    async def fake_collect_sources(session, subject, category=None, limit=6):
        return [{"title": "Brain source", "category": "project", "similarity": 0.98}]

    async def fake_research_topic_brief(**kwargs):
        return None

    async def fake_get_voice_profile(session, profile_name):
        return None

    monkeypatch.setattr(session_bootstrap, "resolve_project", fake_resolve_project)
    monkeypatch.setattr(session_bootstrap, "recompute_project_states", fake_recompute_project_states)
    monkeypatch.setattr(session_bootstrap, "build_project_story_payload", fake_build_project_story_payload)
    monkeypatch.setattr(session_bootstrap, "collect_sources", fake_collect_sources)
    monkeypatch.setattr(session_bootstrap, "research_topic_brief", fake_research_topic_brief)
    monkeypatch.setattr(session_bootstrap.store, "get_voice_profile", fake_get_voice_profile)

    payload = asyncio.run(
        session_bootstrap.build_session_bootstrap(
            object(),
            agent_kind="codex",
            session_id="test-session",
            cwd="/Users/moenuddeenahmadshaik/code/duSraBheja",
            project_hint="duSraBheja",
            include_web=False,
        )
    )

    assert payload["reboot_brief"]["where_it_stands"] == "Brain core exists"
    assert payload["reboot_brief"]["what_changed"] == (
        "Codex closeout: ranking repair | Startup replay now rebuilds planner cards"
    )
    assert payload["reboot_brief"]["what_is_left"] == "Does the fresh digest now focus on current work?"
    assert payload["reboot_brief"]["open_loops"] == ["Does the fresh digest now focus on current work?"]
    assert payload["recent_activity"][0]["entry_type"] == "session_closeout"
    assert all("Knowledge Base:" not in item for item in payload["reboot_brief"]["open_loops"])


def test_record_session_closeout_emits_structured_story_fields(monkeypatch) -> None:
    captured = {}
    project = SimpleNamespace(id=uuid4(), title="duSraBheja")

    async def fake_ingest_source_entries(session, **kwargs):
        captured["entry"] = kwargs["entries"][0]
        return {"items_imported": 1, "projects_touched": ["duSraBheja"]}

    async def fake_resolve_project(session, **kwargs):
        return project

    async def fake_build_project_story_payload(session, project_note_id):
        return {"project": {"id": str(project_note_id), "title": "duSraBheja"}}

    monkeypatch.setattr(session_bootstrap, "ingest_source_entries", fake_ingest_source_entries)
    monkeypatch.setattr(session_bootstrap, "resolve_project", fake_resolve_project)
    monkeypatch.setattr(session_bootstrap, "build_project_story_payload", fake_build_project_story_payload)

    result = asyncio.run(
        session_bootstrap.record_session_closeout(
            object(),
            agent_kind="codex",
            session_id="session-1",
            cwd="/Users/moenuddeenahmadshaik/code/duSraBheja",
            project_ref="duSraBheja",
            summary="Shipped the ranking fix",
            decisions=["Use fresh closeouts first"],
            changes=["Added an active-project fast path"],
            open_questions=["Does the digest now focus on duSraBheja?"],
            source_links=["commit:123"],
            transcript_excerpt="Short excerpt",
        )
    )

    assert captured["entry"]["entry_type"] == "session_closeout"
    assert captured["entry"]["decision"] == "Use fresh closeouts first"
    assert captured["entry"]["outcome"] == "Added an active-project fast path"
    assert captured["entry"]["open_question"] == "Does the digest now focus on duSraBheja?"
    assert result["project"]["title"] == "duSraBheja"
