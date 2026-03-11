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

    payload = asyncio.run(
        digest_service.build_daily_digest_payload(object(), digest_date=date(2026, 3, 11))
    )

    assert payload["tasks"][0]["title"] == "Ship API"
    assert payload["projects"][0]["title"] == "duSraBheja"
    assert payload["projects"][0]["updates"][0]["title"] == "Imported local snapshot"
    assert payload["pending_reviews"][0]["question"] == "Is this a project or a note?"
