from __future__ import annotations

from types import SimpleNamespace

import pytest

import discord

from src.bot import replay


def test_artifact_needs_replay_for_missing_inbox_receipt() -> None:
    artifact = SimpleNamespace(metadata_={})

    assert replay.artifact_needs_replay(
        artifact=artifact,
        channel_name="inbox",
        has_final_classification=True,
    ) is True


def test_artifact_needs_replay_for_missing_planner_card() -> None:
    artifact = SimpleNamespace(metadata_={"discord_receipt_message_id": "123"})

    assert replay.artifact_needs_replay(
        artifact=artifact,
        channel_name="daily-planner",
        has_final_classification=True,
    ) is True


def test_should_skip_empty_message_for_blank_planner_without_image() -> None:
    message = SimpleNamespace(
        content="",
        attachments=[SimpleNamespace(content_type="application/pdf")],
    )

    assert replay.should_skip_empty_message(message, channel_name="daily-planner") is True


def test_should_skip_empty_message_accepts_text_only_planner() -> None:
    message = SimpleNamespace(
        content="Call contractor\nFinish duSraBheja review",
        attachments=[],
    )

    assert replay.should_skip_empty_message(message, channel_name="daily-planner") is False


class _MissingChannel:
    async def fetch_message(self, message_id: int):
        raise discord.NotFound(response=SimpleNamespace(status=404, reason="not found"), message="missing")


class _PresentChannel:
    async def fetch_message(self, message_id: int):
        return SimpleNamespace(id=message_id)


@pytest.mark.asyncio
async def test_artifact_output_missing_on_discord_for_deleted_inbox_receipt() -> None:
    artifact = SimpleNamespace(metadata_={"discord_receipt_message_id": "123"})
    message = SimpleNamespace(channel=_MissingChannel(), guild=None)

    assert await replay.artifact_output_missing_on_discord(
        artifact=artifact,
        message=message,
        channel_name="inbox",
    ) is True


@pytest.mark.asyncio
async def test_artifact_output_missing_on_discord_for_existing_planner_card() -> None:
    planner_channel = _PresentChannel()
    artifact = SimpleNamespace(
        metadata_={
            "discord_planner_card_channel_id": "456",
            "discord_planner_card_message_id": "789",
        }
    )
    guild = SimpleNamespace(get_channel=lambda channel_id: planner_channel)
    message = SimpleNamespace(channel=None, guild=guild)

    assert await replay.artifact_output_missing_on_discord(
        artifact=artifact,
        message=message,
        channel_name="daily-planner",
    ) is False
