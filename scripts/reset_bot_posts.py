#!/usr/bin/env python3
"""Delete this bot's Discord posts so test deploys can start from a clean slate."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import discord

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.bot.cleanup import collect_target_channels, default_brain_channel_names, purge_bot_messages  # noqa: E402
from src.config import settings  # noqa: E402


async def run_cleanup(args: argparse.Namespace) -> dict:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.messages = True

    result_bucket: dict = {}

    class CleanupClient(discord.Client):
        async def on_ready(self) -> None:
            guild = self.get_guild(args.guild_id)
            if guild is None or self.user is None:
                result_bucket.update({"error": "Guild not found in client cache"})
                await self.close()
                return
            channel_names = None
            if not args.all_text_channels:
                channel_names = args.channel or default_brain_channel_names()
            channels = await collect_target_channels(
                guild,
                channel_names=channel_names,
                include_threads=args.include_threads,
                include_archived_threads=args.include_archived_threads,
            )
            result = await purge_bot_messages(
                channels=[item for item in channels if isinstance(item, (discord.TextChannel, discord.Thread))],
                bot_user_id=self.user.id,
                dry_run=not args.execute,
                history_limit=args.history_limit,
            )
            result_bucket.update(result)
            await self.close()

    client = CleanupClient(intents=intents)
    await client.start(settings.discord_token)
    return result_bucket


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Remove bot-authored Discord posts for test resets")
    parser.add_argument("--guild-id", type=int, default=settings.discord_guild_id)
    parser.add_argument("--channel", action="append", help="Channel name to scan. Repeat to target multiple channels.")
    parser.add_argument("--all-text-channels", action="store_true", help="Scan every text channel in the guild.")
    parser.add_argument("--history-limit", type=int, help="Only scan the most recent N messages per channel.")
    parser.add_argument("--include-threads", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-archived-threads", action="store_true")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete messages. Without this flag the script runs as a dry run.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not settings.discord_token:
        raise SystemExit("DISCORD_TOKEN is required in .env to use the reset script.")
    if not args.guild_id:
        raise SystemExit("DISCORD_GUILD_ID or --guild-id is required.")
    result = asyncio.run(run_cleanup(args))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
