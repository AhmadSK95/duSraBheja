"""Helpers for safely removing bot-authored Discord posts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

import discord

from src.config import settings
from src.constants import CATEGORY_CHANNELS


@dataclass(slots=True)
class ChannelCleanupResult:
    channel_id: int
    channel_name: str
    deleted_count: int = 0
    scanned_count: int = 0
    skipped_count: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "deleted_count": self.deleted_count,
            "scanned_count": self.scanned_count,
            "skipped_count": self.skipped_count,
            "errors": list(self.errors or []),
        }


def default_brain_channel_names() -> list[str]:
    names = {
        settings.inbox_channel_name,
        settings.needs_review_channel_name,
        settings.daily_digest_channel_name,
        settings.ask_channel_name,
        *CATEGORY_CHANNELS.values(),
    }
    return sorted(name for name in names if name)


async def collect_target_channels(
    guild: discord.Guild,
    *,
    channel_names: list[str] | None = None,
    include_threads: bool = True,
    include_archived_threads: bool = False,
) -> list[discord.abc.GuildChannel | discord.Thread]:
    targets: list[discord.abc.GuildChannel | discord.Thread] = []
    allowed_names = {name.strip().lower() for name in channel_names or [] if name.strip()}

    for channel in sorted(guild.text_channels, key=lambda item: item.position):
        if allowed_names and channel.name.lower() not in allowed_names:
            continue
        targets.append(channel)
        if include_threads:
            targets.extend(channel.threads)
            if include_archived_threads:
                async for thread in channel.archived_threads(limit=None):
                    targets.append(thread)

    seen: set[int] = set()
    deduped: list[discord.abc.GuildChannel | discord.Thread] = []
    for channel in targets:
        if channel.id in seen:
            continue
        seen.add(channel.id)
        deduped.append(channel)
    return deduped


async def purge_bot_messages(
    *,
    channels: list[discord.TextChannel | discord.Thread],
    bot_user_id: int,
    dry_run: bool = True,
    history_limit: int | None = None,
    after: datetime | None = None,
    before: datetime | None = None,
    pause_seconds: float = 0.35,
) -> dict:
    results: list[ChannelCleanupResult] = []
    total_deleted = 0
    total_scanned = 0

    for channel in channels:
        result = ChannelCleanupResult(
            channel_id=channel.id,
            channel_name=getattr(channel, "name", str(channel.id)),
            errors=[],
        )
        try:
            async for message in channel.history(limit=history_limit, before=before, after=after):
                result.scanned_count += 1
                total_scanned += 1
                if message.author.id != bot_user_id:
                    result.skipped_count += 1
                    continue
                if dry_run:
                    result.deleted_count += 1
                    total_deleted += 1
                    continue
                await message.delete()
                result.deleted_count += 1
                total_deleted += 1
                await asyncio.sleep(pause_seconds)
        except discord.Forbidden:
            result.errors.append("missing permissions")
        except discord.HTTPException as exc:
            result.errors.append(f"http error: {exc.status}")
        results.append(result)

    return {
        "dry_run": dry_run,
        "bot_user_id": bot_user_id,
        "channels": [item.to_dict() for item in results],
        "channels_scanned": len(results),
        "messages_scanned": total_scanned,
        "messages_deleted": total_deleted,
    }
