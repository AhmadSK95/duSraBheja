from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from src.services import digest as digest_service


@dataclass
class FakeBoard:
    payload: dict


@dataclass
class FakeSnapshot:
    project_note_id: str
    status: str = "active"
    manual_state: str = "normal"
    implemented: str | None = "Board-first refactor is underway"
    remaining: str | None = "Deploy the new bot behavior"
    blockers: list[str] = field(default_factory=list)
    holes: list[str] = field(default_factory=lambda: ["OCR validation still needs tightening"])
    what_changed: str | None = "Validation pipeline landed"


@dataclass
class FakeNote:
    id: str
    title: str
    priority: str = "medium"


@dataclass
class FakeReminder:
    id: str
    title: str
    next_fire_at: datetime | None = None


def test_build_daily_digest_payload_uses_latest_daily_board(monkeypatch) -> None:
    async def fake_get_latest_board(session, *, board_type, generated_for_date=None):
        assert board_type == "daily"
        assert generated_for_date == date(2026, 3, 12)
        return FakeBoard(
            payload={
                "story": "March 12 shipped important ingestion cleanup and board-first groundwork.",
                "carry_forward": ["Deploy the board-first changes", "Verify the moderation dashboard"],
                "project_signals": [{"project": "duSraBheja", "summary": "Validated captures are now being gated before publishing."}],
            }
        )

    async def fake_recompute_project_states(session):
        return []

    async def fake_list_project_state_snapshots(session, limit=20):
        return [FakeSnapshot("project-1")]

    async def fake_get_note(session, note_id):
        assert note_id == "project-1"
        return FakeNote("project-1", "duSraBheja")

    async def fake_list_reminders(session, status="active", limit=50):
        return [FakeReminder("r1", "Call Annie", datetime(2026, 3, 13, 13, 0, tzinfo=timezone.utc))]

    async def fake_list_notes(session, category=None, limit=12, status="active"):
        assert category == "task"
        return [FakeNote("t1", "Clean up Discord bot outputs")]

    monkeypatch.setattr(digest_service.store, "get_latest_board", fake_get_latest_board)
    monkeypatch.setattr(digest_service.store, "list_project_state_snapshots", fake_list_project_state_snapshots)
    monkeypatch.setattr(digest_service.store, "get_note", fake_get_note)
    monkeypatch.setattr(digest_service.store, "list_reminders", fake_list_reminders)
    monkeypatch.setattr(digest_service.store, "list_notes", fake_list_notes)
    monkeypatch.setattr(digest_service, "recompute_project_states", fake_recompute_project_states)

    payload = asyncio.run(digest_service.build_daily_digest_payload(object(), digest_date=date(2026, 3, 13)))

    assert payload["board_date"] == "2026-03-12"
    assert "March 12 shipped" in payload["summary"]
    assert payload["project_status"][0]["project"] == "duSraBheja"
    assert payload["possible_tasks"][0]["title"] == "Deploy the board-first changes"
    assert payload["possible_tasks"][1]["title"] == "Verify the moderation dashboard"
    assert payload["reminders_due_today"][0]["title"] == "Call Annie"


def test_build_daily_digest_payload_generates_missing_board(monkeypatch) -> None:
    calls = {}

    async def fake_get_latest_board(session, *, board_type, generated_for_date=None):
        if calls.get("generated"):
            return FakeBoard(payload={"story": "Fresh board", "carry_forward": [], "project_signals": []})
        return None

    async def fake_generate_or_refresh_board(session, *, window):
        calls["generated"] = window.generated_for_date.isoformat()
        return {"story": "Fresh board", "carry_forward": [], "project_signals": []}

    async def fake_recompute_project_states(session):
        return []

    async def fake_list_project_state_snapshots(session, limit=20):
        return []

    async def fake_list_reminders(session, status="active", limit=50):
        return []

    async def fake_list_notes(session, category=None, limit=12, status="active"):
        return []

    monkeypatch.setattr(digest_service.store, "get_latest_board", fake_get_latest_board)
    monkeypatch.setattr(digest_service.store, "list_project_state_snapshots", fake_list_project_state_snapshots)
    monkeypatch.setattr(digest_service.store, "list_reminders", fake_list_reminders)
    monkeypatch.setattr(digest_service.store, "list_notes", fake_list_notes)
    monkeypatch.setattr(digest_service, "generate_or_refresh_board", fake_generate_or_refresh_board)
    monkeypatch.setattr(digest_service, "recompute_project_states", fake_recompute_project_states)

    payload = asyncio.run(digest_service.build_daily_digest_payload(object(), digest_date=date(2026, 3, 13)))

    assert calls["generated"] == "2026-03-12"
    assert payload["summary"] == "Fresh board"
