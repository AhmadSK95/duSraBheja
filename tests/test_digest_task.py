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


def test_generate_daily_digest_publishes_trigger_metadata(monkeypatch) -> None:
    published = {}

    async def _fake_generate_or_refresh_digest(session, *, digest_date):
        return {
            "digest_date": digest_date.isoformat(),
            "tasks": [],
            "projects": [],
            "recent_activity": [],
            "pending_reviews": [],
            "open_loops": [],
            "story_connections": [],
            "writing_topics": [],
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
