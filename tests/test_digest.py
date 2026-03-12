from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from types import SimpleNamespace

from src.services import digest as digest_service


@dataclass
class FakeNote:
    id: str
    title: str
    priority: str = "medium"
    status: str = "active"
    category: str = "project"
    content: str | None = None
    metadata_: dict = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc))


@dataclass
class FakeReview:
    id: str
    question: str


@dataclass
class FakeEntry:
    id: str
    title: str
    entry_type: str
    actor_name: str
    happened_at: datetime
    project_note_id: str | None = None
    decision: str | None = None
    impact: str | None = None
    open_question: str | None = None


@dataclass
class FakeSnapshot:
    project_note_id: str
    active_score: float
    status: str = "active"
    manual_state: str = "normal"
    confidence: float = 0.8
    implemented: str | None = "API import path exists"
    remaining: str | None = "Need deeper digest intelligence"
    holes: list[str] = field(default_factory=lambda: ["Still thin on morning brief coverage"])
    risks: list[str] = field(default_factory=list)
    what_changed: str | None = "Imported local snapshot"


@dataclass
class FakeReminder:
    id: str
    title: str
    next_fire_at: datetime | None = None


@dataclass
class FakePreference:
    sections: dict


def test_build_daily_digest_payload_aggregates_tasks_projects_and_activity(monkeypatch) -> None:
    async def fake_list_notes(session, category=None, limit=25, status="active"):
        if category == "task":
            return [FakeNote("task-1", "Ship API")]
        if category == "project":
            return [FakeNote("project-1", "duSraBheja", content="Builder brain project")]
        return []

    async def fake_list_recent_activity(session, limit=25, project_note_id=None):
        return [
            FakeEntry(
                id="entry-1",
                title="Imported local snapshot",
                entry_type="progress_update",
                actor_name="macbook",
                happened_at=datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc),
                project_note_id="project-1",
            )
        ]

    async def fake_get_pending_reviews(session):
        return [FakeReview("review-1", "Is this a project or a note?")]

    async def fake_get_note(session, note_id):
        if note_id == "project-1":
            return FakeNote("project-1", "duSraBheja", content="Builder brain project")
        return None

    async def fake_list_reminders(session, status="active", limit=50):
        return [FakeReminder("reminder-1", "Trash duty", datetime(2026, 3, 11, 22, 0, tzinfo=timezone.utc))]

    async def fake_list_project_state_snapshots(session, limit=25, statuses=None):
        return [FakeSnapshot("project-1", 0.91)]

    async def fake_upsert_digest_preference(session, profile_name, timezone_name, sections, metadata_):
        return FakePreference(sections=sections)

    async def fake_get_digest_preference(session, profile_name="default"):
        return FakePreference(sections={"headline": True})

    async def fake_list_story_connections(session, limit=20):
        return []

    async def fake_get_voice_profile(session, profile_name="ahmad-default"):
        return None

    async def fake_recompute_project_states(session):
        return []

    async def fake_search_youtube_learning_queries(*, topics):
        return []

    fake_store = SimpleNamespace(
        list_notes=fake_list_notes,
        list_recent_activity=fake_list_recent_activity,
        get_pending_reviews=fake_get_pending_reviews,
        get_note=fake_get_note,
        list_reminders=fake_list_reminders,
        list_project_state_snapshots=fake_list_project_state_snapshots,
        upsert_digest_preference=fake_upsert_digest_preference,
        get_digest_preference=fake_get_digest_preference,
        list_story_connections=fake_list_story_connections,
    )
    monkeypatch.setattr(digest_service, "store", fake_store)
    async def fake_recompute_project_states(session):
        return []

    async def fake_search_youtube_learning_queries(*, topics):
        return []

    monkeypatch.setattr(digest_service, "recompute_project_states", fake_recompute_project_states)
    monkeypatch.setattr(digest_service, "search_youtube_learning_queries", fake_search_youtube_learning_queries)

    async def fake_compose_digest_sections(session, *, digest_date, trigger, context_text):
        assert digest_date == "2026-03-11"
        assert trigger == "scheduled"
        assert "duSraBheja" in context_text
        return {
            "headline": "Morning operating brief",
            "narrative": "duSraBheja is active and the API import is the freshest turning point.",
            "recommended_tasks": [{"title": "Ship API", "why": "Highest leverage now", "project_ref": "duSraBheja"}],
            "project_assessments": [
                {
                    "project": "duSraBheja",
                    "where_it_stands": "Active",
                    "implemented": "API import path exists",
                    "left": "Need deeper digest intelligence",
                    "holes": "Still thin on morning brief coverage",
                    "next_step": "Improve digest synthesis",
                }
            ],
            "writing_topics": [{"title": "Story-first retrieval", "why": "Active product theme"}],
            "video_recommendations": [
                {
                    "title": "RAG critique walkthrough",
                    "search_query": "rag critique walkthrough",
                    "why": "Useful for current project work",
                }
            ],
            "brain_teasers": [{"title": "Edge case", "prompt": "What fails first?", "hint": "Check the scheduler"}],
            "improvement_focus": [{"title": "Tighten digest", "why": "The brief is still noisy"}],
            "low_confidence_sections": [],
        }

    monkeypatch.setattr(digest_service, "compose_digest_sections", fake_compose_digest_sections)

    payload = asyncio.run(
        digest_service.build_daily_digest_payload(object(), digest_date=date(2026, 3, 11))
    )

    assert payload["headline"] == "Morning operating brief"
    assert payload["recommended_tasks"][0]["title"] == "Ship API"
    assert payload["tasks"][0]["title"] == "Ship API"
    assert payload["projects"][0]["title"] == "duSraBheja"
    assert payload["projects"][0]["active_score"] == 0.91
    assert payload["projects"][0]["updates"][0]["title"] == "Imported local snapshot"
    assert payload["pending_reviews"][0]["question"] == "Is this a project or a note?"
    assert payload["video_recommendations"][0]["search_query"] == "rag critique walkthrough"
    assert payload["improvement_focus"][0]["title"] == "Tighten digest"


def test_build_daily_digest_payload_skips_collector_only_stale_projects(monkeypatch) -> None:
    async def fake_list_notes(session, category=None, limit=25, status="active"):
        if category in {"task", "resource"}:
            return []
        return []

    async def fake_list_recent_activity(session, limit=25, project_note_id=None):
        return [
            FakeEntry(
                id="entry-collector",
                title="hadoop local snapshot",
                entry_type="context_dump",
                actor_name="macbook",
                happened_at=datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc),
                project_note_id="project-stale",
            )
        ]

    async def fake_get_pending_reviews(session):
        return []

    async def fake_get_note(session, note_id):
        if note_id == "project-active":
            return FakeNote("project-active", "duSraBheja", content="Current project")
        if note_id == "project-stale":
            return FakeNote("project-stale", "hadoop-single-node-cluster", content="Old local repo")
        return None

    async def fake_list_reminders(session, status="active", limit=50):
        return []

    async def fake_list_project_state_snapshots(session, limit=25, statuses=None):
        return [FakeSnapshot("project-active", 0.88)]

    async def fake_upsert_digest_preference(session, profile_name, timezone_name, sections, metadata_):
        return FakePreference(sections=sections)

    async def fake_get_digest_preference(session, profile_name="default"):
        return FakePreference(sections={"headline": True})

    async def fake_list_story_connections(session, limit=20):
        return []

    async def fake_get_voice_profile(session, profile_name="ahmad-default"):
        return None

    async def fake_recompute_project_states(session):
        return []

    async def fake_search_youtube_learning_queries(*, topics):
        return []

    fake_store = SimpleNamespace(
        list_notes=fake_list_notes,
        list_recent_activity=fake_list_recent_activity,
        get_pending_reviews=fake_get_pending_reviews,
        get_note=fake_get_note,
        list_reminders=fake_list_reminders,
        list_project_state_snapshots=fake_list_project_state_snapshots,
        upsert_digest_preference=fake_upsert_digest_preference,
        get_digest_preference=fake_get_digest_preference,
        list_story_connections=fake_list_story_connections,
        get_voice_profile=fake_get_voice_profile,
    )
    monkeypatch.setattr(digest_service, "store", fake_store)
    monkeypatch.setattr(digest_service, "recompute_project_states", fake_recompute_project_states)
    monkeypatch.setattr(digest_service, "search_youtube_learning_queries", fake_search_youtube_learning_queries)

    async def fake_compose_digest_sections(session, *, digest_date, trigger, context_text):
        return {
            "headline": "Morning brief",
            "narrative": "Focused on current work only.",
            "recommended_tasks": [],
            "project_assessments": [],
            "writing_topics": [],
            "video_recommendations": [],
            "brain_teasers": [],
            "improvement_focus": [],
            "low_confidence_sections": [],
        }

    monkeypatch.setattr(digest_service, "compose_digest_sections", fake_compose_digest_sections)

    payload = asyncio.run(
        digest_service.build_daily_digest_payload(object(), digest_date=date(2026, 3, 11))
    )

    assert [item["title"] for item in payload["projects"]] == ["duSraBheja"]
