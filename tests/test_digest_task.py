from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from src.worker.tasks import digest as digest_task


def test_generate_scheduled_digest_tick_skips_until_target_hour(monkeypatch) -> None:
    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 12, 7, 0, tzinfo=tz)

    monkeypatch.setattr(digest_task, "datetime", _FakeDateTime)
    monkeypatch.setattr(digest_task.settings, "digest_cron_hour", 8)
    monkeypatch.setattr(digest_task.settings, "digest_timezone", "America/New_York")

    result = asyncio.run(digest_task.generate_scheduled_digest_tick(SimpleNamespace()))

    assert result["status"] == "skipped"


def test_generate_scheduled_digest_tick_catches_up_after_target_hour(monkeypatch) -> None:
    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 12, 9, 0, tzinfo=tz)

    class _FakeRedis:
        def __init__(self):
            self.claimed = False

        async def set(self, key, value, ex=None, nx=False):
            assert key == "brain:digest:scheduled:2026-03-12"
            assert nx is True
            if self.claimed:
                return False
            self.claimed = True
            return True

        async def delete(self, key):
            return 1

        async def aclose(self):
            return None

    captured = {}

    async def _fake_generate_daily_digest(ctx, *, trigger="scheduled", reason=None, metadata=None):
        captured["trigger"] = trigger
        return {"status": "generated"}

    async def _fake_generate_daily_board(ctx, *, run_date=None):
        captured["daily_board"] = run_date
        return {"status": "board-generated"}

    async def _fake_generate_weekly_board(ctx, *, run_date=None):
        captured["weekly_board"] = run_date
        return {"status": "weekly-board-generated"}

    redis_instance = _FakeRedis()
    monkeypatch.setattr(digest_task, "datetime", _FakeDateTime)
    monkeypatch.setattr(digest_task.settings, "digest_cron_hour", 8)
    monkeypatch.setattr(digest_task.settings, "digest_timezone", "America/New_York")
    monkeypatch.setattr(digest_task.settings, "weekly_board_cron_weekday", 0)
    monkeypatch.setattr(digest_task.Redis, "from_url", lambda url: redis_instance)
    monkeypatch.setattr(digest_task, "generate_daily_digest", _fake_generate_daily_digest)
    monkeypatch.setattr(digest_task, "generate_daily_board", _fake_generate_daily_board)
    monkeypatch.setattr(digest_task, "generate_weekly_board", _fake_generate_weekly_board)

    result = asyncio.run(digest_task.generate_scheduled_digest_tick(SimpleNamespace()))

    assert result == {"status": "generated"}
    assert captured["trigger"] == "scheduled"
    assert captured["daily_board"] == "2026-03-12"
    assert "weekly_board" not in captured


def test_generate_daily_digest_publishes_trigger_metadata(monkeypatch) -> None:
    published = {}

    async def _fake_generate_or_refresh_digest(session, *, digest_date, trigger="scheduled"):
        return {
            "digest_date": digest_date.isoformat(),
            "headline": "Morning brief",
            "summary": "Board-grounded morning brief.",
            "board_date": "2026-03-11",
            "project_status": [],
            "possible_tasks": [],
            "reminders_due_today": [],
        }

    async def _fake_publish_event(channel: str, payload: dict):
        published["channel"] = channel
        published["payload"] = payload

    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 12, 8, 0, tzinfo=tz)

    class _FakeSessionManager:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(digest_task, "datetime", _FakeDateTime)
    monkeypatch.setattr(digest_task, "generate_or_refresh_digest", _fake_generate_or_refresh_digest)
    monkeypatch.setattr(digest_task, "publish_notification", _fake_publish_event)
    monkeypatch.setattr(digest_task, "async_session", lambda: _FakeSessionManager())
    monkeypatch.setattr(digest_task.settings, "digest_timezone", "America/New_York")

    asyncio.run(
        digest_task.generate_daily_digest(
            SimpleNamespace(),
            trigger="story_pulse",
            reason="codex_history:sync",
            metadata={"items_imported": 4},
        )
    )

    assert published["payload"]["trigger"] == "story_pulse"
    assert published["payload"]["reason"] == "codex_history:sync"
    assert published["payload"]["metadata"] == {"items_imported": 4}
    assert published["payload"]["headline"] == "Morning brief"
