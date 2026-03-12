from __future__ import annotations

import pytest

from src.worker.main import (
    JOB_PROCESS_INBOX_MESSAGE,
    JOB_CLASSIFY_ARTIFACT,
    JOB_RECLASSIFY_ARTIFACT,
    JOB_GENERATE_DAILY_DIGEST,
    enqueue_classify,
    enqueue_ingest,
    enqueue_reclassify,
    enqueue_story_pulse_digest,
    publish_event,
)


class _FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.published: list[tuple[str, str]] = []
        self.locks: dict[str, str] = {}

    async def enqueue_job(self, job_name: str, **kwargs):
        self.calls.append((job_name, kwargs))

    async def publish(self, channel: str, payload: str):
        self.published.append((channel, payload))

    async def set(self, key: str, value: str, ex: int, nx: bool):
        if nx and key in self.locks:
            return False
        self.locks[key] = value
        return True


@pytest.mark.asyncio
async def test_enqueue_ingest_uses_registered_job_name(monkeypatch) -> None:
    pool = _FakePool()

    async def _fake_get_pool():
        return pool

    monkeypatch.setattr("src.worker.main.get_pool", _fake_get_pool)

    await enqueue_ingest(
        discord_message_id="123",
        discord_channel_id="456",
        text="hello",
        attachments=[],
        force_category="note",
        source="discord",
    )

    assert pool.calls == [
        (
            JOB_PROCESS_INBOX_MESSAGE,
            {
                "discord_message_id": "123",
                "discord_channel_id": "456",
                "text": "hello",
                "attachments": [],
                "force_category": "note",
                "source": "discord",
            },
        )
    ]


@pytest.mark.asyncio
async def test_enqueue_reclassify_uses_registered_job_name(monkeypatch) -> None:
    pool = _FakePool()

    async def _fake_get_pool():
        return pool

    monkeypatch.setattr("src.worker.main.get_pool", _fake_get_pool)

    await enqueue_reclassify("artifact-id", "answer")

    assert pool.calls == [
        (
            JOB_RECLASSIFY_ARTIFACT,
            {
                "artifact_id": "artifact-id",
                "user_answer": "answer",
            },
        )
    ]


@pytest.mark.asyncio
async def test_enqueue_classify_uses_registered_job_name(monkeypatch) -> None:
    pool = _FakePool()

    async def _fake_get_pool():
        return pool

    monkeypatch.setattr("src.worker.main.get_pool", _fake_get_pool)

    await enqueue_classify("artifact-id", force_category="daily_planner")

    assert pool.calls == [
        (
            JOB_CLASSIFY_ARTIFACT,
            {
                "artifact_id": "artifact-id",
                "force_category": "daily_planner",
            },
        )
    ]


@pytest.mark.asyncio
async def test_publish_event_uses_pool_publish(monkeypatch) -> None:
    pool = _FakePool()

    async def _fake_get_pool():
        return pool

    monkeypatch.setattr("src.worker.main.get_pool", _fake_get_pool)

    await publish_event("brain:test", {"ok": True})

    assert pool.published == [('brain:test', '{"ok": true}')]


@pytest.mark.asyncio
async def test_enqueue_story_pulse_digest_debounces_duplicate_requests(monkeypatch) -> None:
    pool = _FakePool()

    async def _fake_get_pool():
        return pool

    monkeypatch.setattr("src.worker.main.get_pool", _fake_get_pool)
    monkeypatch.setattr("src.worker.main.settings.digest_story_pulse_cooldown_minutes", 15)

    first = await enqueue_story_pulse_digest(reason="codex_history:sync", metadata={"items_imported": 4})
    second = await enqueue_story_pulse_digest(reason="codex_history:sync", metadata={"items_imported": 4})

    assert first is True
    assert second is False
    assert pool.calls == [
        (
            JOB_GENERATE_DAILY_DIGEST,
            {
                "trigger": "story_pulse",
                "reason": "codex_history:sync",
                "metadata": {"items_imported": 4},
            },
        )
    ]
