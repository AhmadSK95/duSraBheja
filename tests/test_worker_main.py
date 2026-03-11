from __future__ import annotations

import pytest

from src.worker.main import (
    JOB_PROCESS_INBOX_MESSAGE,
    JOB_CLASSIFY_ARTIFACT,
    JOB_RECLASSIFY_ARTIFACT,
    enqueue_classify,
    enqueue_ingest,
    enqueue_reclassify,
    publish_event,
)


class _FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.published: list[tuple[str, str]] = []

    async def enqueue_job(self, job_name: str, **kwargs):
        self.calls.append((job_name, kwargs))

    async def publish(self, channel: str, payload: str):
        self.published.append((channel, payload))


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
