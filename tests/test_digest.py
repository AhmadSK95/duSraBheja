from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from src.bot.cogs.inbox import build_board_embed, build_digest_embeds
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

    async def fake_list_public_surface_reviews(session, status=None, limit=20):
        return []

    async def fake_list_improvement_opportunities(session, limit=20):
        return []

    monkeypatch.setattr(digest_service.store, "get_latest_board", fake_get_latest_board)
    monkeypatch.setattr(digest_service.store, "list_project_state_snapshots", fake_list_project_state_snapshots)
    monkeypatch.setattr(digest_service.store, "get_note", fake_get_note)
    monkeypatch.setattr(digest_service.store, "list_reminders", fake_list_reminders)
    monkeypatch.setattr(digest_service.store, "list_notes", fake_list_notes)
    monkeypatch.setattr(digest_service, "recompute_project_states", fake_recompute_project_states)
    monkeypatch.setattr(digest_service, "list_public_surface_reviews", fake_list_public_surface_reviews)
    monkeypatch.setattr(digest_service, "list_improvement_opportunities", fake_list_improvement_opportunities)

    payload = asyncio.run(digest_service.build_daily_digest_payload(object(), digest_date=date(2026, 3, 13)))

    assert payload["board_date"] == "2026-03-12"
    assert "March 12 shipped" in payload["summary"]
    assert payload["project_status"][0]["project"] == "duSraBheja"
    assert payload["possible_tasks"][0]["title"] == "Deploy the board-first changes"
    assert payload["possible_tasks"][1]["title"] == "Verify the moderation dashboard"
    assert payload["priority_moves"][0]["lane"] == "project"
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

    async def fake_list_public_surface_reviews(session, status=None, limit=20):
        return []

    async def fake_list_improvement_opportunities(session, limit=20):
        return []

    monkeypatch.setattr(digest_service.store, "get_latest_board", fake_get_latest_board)
    monkeypatch.setattr(digest_service.store, "list_project_state_snapshots", fake_list_project_state_snapshots)
    monkeypatch.setattr(digest_service.store, "list_reminders", fake_list_reminders)
    monkeypatch.setattr(digest_service.store, "list_notes", fake_list_notes)
    monkeypatch.setattr(digest_service, "generate_or_refresh_board", fake_generate_or_refresh_board)
    monkeypatch.setattr(digest_service, "recompute_project_states", fake_recompute_project_states)
    monkeypatch.setattr(digest_service, "list_public_surface_reviews", fake_list_public_surface_reviews)
    monkeypatch.setattr(digest_service, "list_improvement_opportunities", fake_list_improvement_opportunities)

    payload = asyncio.run(digest_service.build_daily_digest_payload(object(), digest_date=date(2026, 3, 13)))

    assert calls["generated"] == "2026-03-12"
    assert payload["summary"] == "Fresh board"


def test_digest_and_board_embeds_fit_discord_field_limits() -> None:
    board_embed = build_board_embed(
        {
            "board_type": "daily",
            "coverage_label": "Friday, Mar 13, 2026",
            "story": "x" * 3500,
            "what_mattered": ["y" * 300 for _ in range(8)],
            "carry_forward": ["z" * 300 for _ in range(8)],
            "project_signals": [{"project": f"project-{idx}", "summary": "w" * 300} for idx in range(6)],
            "source_count": 12,
            "excluded_count": 2,
        }
    )
    digest_embeds = build_digest_embeds(
        {
            "digest_date": "2026-03-13",
            "summary": "x" * 3500,
            "board_date": "2026-03-12",
            "project_status": [
                {
                    "project": f"project-{idx}",
                    "where_it_stands": "a" * 300,
                    "what_changed": "b" * 300,
                    "blocked_or_unclear": "c" * 300,
                    "best_next_move": "d" * 300,
                }
                for idx in range(5)
            ],
            "possible_tasks": [{"title": "t" * 250, "why": "u" * 250} for _ in range(8)],
            "priority_moves": [{"title": "p" * 250, "why": "q" * 250, "lane": "project"} for _ in range(6)],
            "review_queue": [{"subject_type": "project", "subject": "dusrabheja", "summary": "r" * 250} for _ in range(5)],
            "improvement_watchlist": [{"title": "watch" * 40, "severity": "high", "summary": "s" * 250} for _ in range(4)],
            "reminders_due_today": [{"title": "r" * 250, "next_fire_at": "today"} for _ in range(8)],
        }
    )

    for embed in [board_embed, *digest_embeds]:
        for embed_field in embed.fields:
            assert len(embed_field.value) <= 1024


def test_daily_digest_review_queue_filters_internal_cycle_reviews(monkeypatch) -> None:
    async def fake_get_latest_board(session, *, board_type, generated_for_date=None):
        return FakeBoard(payload={"story": "Fresh board", "carry_forward": [], "project_signals": []})

    async def fake_recompute_project_states(session):
        return []

    async def fake_list_project_state_snapshots(session, limit=20):
        return []

    async def fake_list_reminders(session, status="active", limit=50):
        return []

    async def fake_list_notes(session, category=None, limit=12, status="active"):
        return []

    class Review:
        def __init__(self, subject_type: str, subject_slug: str, diff_summary: str):
            self.subject_type = subject_type
            self.subject_slug = subject_slug
            self.diff_summary = diff_summary

    async def fake_list_public_surface_reviews(session, status=None, limit=20):
        return [
            Review("project", "dusrabheja", "Refresh the flagship update window."),
            Review("campaign-wave", "wave-3", "Wave approval should stay out of Discord review."),
            Review("internal-cycle", "cycle-7", "Internal cycle record should stay hidden."),
        ]

    async def fake_list_improvement_opportunities(session, limit=20):
        return []

    monkeypatch.setattr(digest_service.store, "get_latest_board", fake_get_latest_board)
    monkeypatch.setattr(digest_service.store, "list_project_state_snapshots", fake_list_project_state_snapshots)
    monkeypatch.setattr(digest_service.store, "list_reminders", fake_list_reminders)
    monkeypatch.setattr(digest_service.store, "list_notes", fake_list_notes)
    monkeypatch.setattr(digest_service, "recompute_project_states", fake_recompute_project_states)
    monkeypatch.setattr(digest_service, "list_public_surface_reviews", fake_list_public_surface_reviews)
    monkeypatch.setattr(digest_service, "list_improvement_opportunities", fake_list_improvement_opportunities)

    payload = asyncio.run(digest_service.build_daily_digest_payload(object(), digest_date=date(2026, 3, 13)))

    assert payload["review_queue"] == [
        {
            "subject": "dusrabheja",
            "subject_type": "project",
            "summary": "Refresh the flagship update window.",
        }
    ]
