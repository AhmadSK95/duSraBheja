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


def test_build_daily_digest_payload_aggregates_tasks_projects_and_activity(monkeypatch) -> None:
    async def fake_list_notes(session, category=None, limit=25, status="active"):
        if category == "task":
            return [FakeNote("task-1", "Ship API")]
        if category == "project":
            return [FakeNote("project-1", "duSraBheja")]
        return []

    async def fake_list_recent_activity(session, limit=25, project_note_id=None):
        return [
            FakeEntry(
                id="entry-1",
                title="Imported local snapshot",
                entry_type="context_dump",
                actor_name="macbook",
                happened_at=datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc),
                project_note_id="project-1",
            )
        ]

    async def fake_get_pending_reviews(session):
        return [FakeReview("review-1", "Is this a project or a note?")]

    fake_store = SimpleNamespace(
        list_notes=fake_list_notes,
        list_recent_activity=fake_list_recent_activity,
        get_pending_reviews=fake_get_pending_reviews,
    )
    monkeypatch.setattr(digest_service, "store", fake_store)

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
        }

    monkeypatch.setattr(digest_service, "compose_digest_sections", fake_compose_digest_sections)

    payload = asyncio.run(
        digest_service.build_daily_digest_payload(object(), digest_date=date(2026, 3, 11))
    )

    assert payload["headline"] == "Morning operating brief"
    assert payload["recommended_tasks"][0]["title"] == "Ship API"
    assert payload["tasks"][0]["title"] == "Ship API"
    assert payload["projects"][0]["title"] == "duSraBheja"
    assert payload["projects"][0]["updates"][0]["title"] == "Imported local snapshot"
    assert payload["pending_reviews"][0]["question"] == "Is this a project or a note?"
    assert payload["video_recommendations"][0]["search_query"] == "rag critique walkthrough"
