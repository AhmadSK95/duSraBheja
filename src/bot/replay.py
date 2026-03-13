"""Startup and manual replay helpers for repairing Discord ingestion outputs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import discord
from discord.ext import commands

from src.config import settings
from src.database import async_session
from src.lib.store import get_artifact_by_discord_id, get_latest_classification, reset_artifact_processing
from src.worker.main import enqueue_classify, enqueue_ingest

log = logging.getLogger("brain-bot.replay")

PLANNER_CHANNEL_CATEGORIES = {}
DEFAULT_REPLAY_CHANNELS = ("inbox",)


@dataclass(slots=True)
class ReplayStats:
    scanned_messages: int = 0
    queued_new: int = 0
    requeued_existing: int = 0
    skipped_existing: int = 0
    skipped_non_target: int = 0
    skipped_empty: int = 0
    skipped_missing_channel: int = 0
    channel_counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "scanned_messages": self.scanned_messages,
            "queued_new": self.queued_new,
            "requeued_existing": self.requeued_existing,
            "skipped_existing": self.skipped_existing,
            "skipped_non_target": self.skipped_non_target,
            "skipped_empty": self.skipped_empty,
            "skipped_missing_channel": self.skipped_missing_channel,
            "channel_counts": dict(self.channel_counts),
        }


def replay_author_ids() -> set[int]:
    values = {
        int(item.strip())
        for item in (settings.startup_replay_author_ids or "").split(",")
        if item.strip().isdigit()
    }
    return values


def replay_channel_names() -> list[str]:
    values = [item.strip() for item in (settings.startup_replay_channel_names or "").split(",") if item.strip()]
    return values or list(DEFAULT_REPLAY_CHANNELS)


def force_category_for_channel(channel_name: str) -> str | None:
    return PLANNER_CHANNEL_CATEGORIES.get(channel_name)


def should_replay_author(author_id: int, *, allowed_author_ids: set[int]) -> bool:
    return not allowed_author_ids or author_id in allowed_author_ids


def should_skip_empty_message(message: discord.Message, *, channel_name: str) -> bool:
    if message.content.strip():
        return False
    if channel_name in PLANNER_CHANNEL_CATEGORIES:
        return not any((attachment.content_type or "").startswith("image/") for attachment in message.attachments)
    return not message.attachments


def artifact_needs_replay(*, artifact, channel_name: str, has_any_classification: bool) -> bool:
    return not has_any_classification


async def _discord_message_exists(channel, message_id: str | None) -> bool:
    if not channel or not message_id:
        return False
    try:
        await channel.fetch_message(int(message_id))
        return True
    except (discord.NotFound, ValueError, TypeError):
        return False
    except (discord.Forbidden, discord.HTTPException):
        # If Discord is temporarily unhappy, avoid destructive replay churn.
        return True


async def artifact_output_missing_on_discord(*, artifact, message: discord.Message, channel_name: str) -> bool:
    return False


def attachment_payloads(message: discord.Message) -> list[dict]:
    return [
        {
            "url": attachment.url,
            "filename": attachment.filename,
            "content_type": attachment.content_type or "application/octet-stream",
            "size": attachment.size,
        }
        for attachment in message.attachments
    ]


async def reconcile_message(message: discord.Message) -> str:
    channel_name = getattr(message.channel, "name", "")
    force_category = force_category_for_channel(channel_name)
    attachments = attachment_payloads(message)
    payload_text = message.content or (f"[{channel_name} image]" if attachments and force_category else "")

    async with async_session() as session:
        artifact = await get_artifact_by_discord_id(session, str(message.id))
        if artifact:
            latest_classification = await get_latest_classification(session, artifact.id)
            needs_replay = artifact_needs_replay(
                artifact=artifact,
                channel_name=channel_name,
                has_any_classification=bool(latest_classification),
            )
            if not needs_replay:
                needs_replay = await artifact_output_missing_on_discord(
                    artifact=artifact,
                    message=message,
                    channel_name=channel_name,
                )
            if not needs_replay:
                return "skipped_existing"
            await reset_artifact_processing(session, artifact.id)
            await enqueue_classify(str(artifact.id), force_category=force_category)
            return "requeued_existing"

    await enqueue_ingest(
        discord_message_id=str(message.id),
        discord_channel_id=str(message.channel.id),
        text=payload_text,
        attachments=attachments,
        force_category=force_category,
        source="discord",
        metadata={"channel_name": channel_name, "capture_context": "startup_replay"},
    )
    return "queued_new"


async def replay_discord_history(
    bot: commands.Bot,
    *,
    channel_names: list[str] | None = None,
    history_limit: int | None = None,
    allowed_author_ids: set[int] | None = None,
) -> ReplayStats:
    await bot.wait_until_ready()
    guild = bot.get_guild(settings.discord_guild_id)
    if guild is None:
        guild = await bot.fetch_guild(settings.discord_guild_id)

    names = [item.lower() for item in (channel_names or replay_channel_names())]
    channels = {channel.name.lower(): channel for channel in getattr(guild, "text_channels", [])}
    stats = ReplayStats()
    authors = allowed_author_ids if allowed_author_ids is not None else replay_author_ids()
    limit = history_limit if history_limit not in (None, 0) else None

    for name in names:
        channel = channels.get(name)
        if channel is None:
            stats.skipped_missing_channel += 1
            continue

        processed_for_channel = 0
        async for message in channel.history(limit=limit, oldest_first=True):
            stats.scanned_messages += 1
            if message.author.bot:
                continue
            if not should_replay_author(message.author.id, allowed_author_ids=authors):
                stats.skipped_non_target += 1
                continue
            if should_skip_empty_message(message, channel_name=name):
                stats.skipped_empty += 1
                continue

            outcome = await reconcile_message(message)
            processed_for_channel += 1
            if outcome == "queued_new":
                stats.queued_new += 1
            elif outcome == "requeued_existing":
                stats.requeued_existing += 1
            elif outcome == "skipped_existing":
                stats.skipped_existing += 1
        stats.channel_counts[name] = processed_for_channel

    log.info("Discord replay completed: %s", stats.as_dict())
    return stats
