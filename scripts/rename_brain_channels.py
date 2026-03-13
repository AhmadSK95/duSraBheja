#!/usr/bin/env python3
"""Rename legacy Discord brain channels to the board-first names."""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from src.config import settings

CHANNEL_RENAMES = {
    "daily-planner": {
        "name": settings.daily_board_channel_name,
        "topic": "Yesterday's validated story board, generated each morning",
    },
    "weekly-planner": {
        "name": settings.weekly_board_channel_name,
        "topic": "Weekly narrative board covering the fully closed previous week",
    },
    "daily-digest": {
        "name": settings.daily_digest_channel_name,
        "topic": "Simple morning brief with project status, possible tasks, and reminders",
    },
    "ask-brain": {
        "name": settings.ask_channel_name,
        "topic": "Ask the brain questions here",
    },
    "inbox": {
        "name": settings.inbox_channel_name,
        "topic": "Drop anything here — the brain will classify and store it silently",
    },
}


def _find_channel(channels: list[dict], *names: str) -> dict | None:
    wanted = {name.strip().lower() for name in names if name.strip()}
    for channel in channels:
        if channel.get("name", "").lower() in wanted:
            return channel
    return None


async def main() -> None:
    parser = argparse.ArgumentParser(description="Rename Discord brain channels to the board-first layout.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    headers = {
        "Authorization": f"Bot {settings.discord_token}",
        "Content-Type": "application/json",
    }

    renamed: list[dict] = []
    async with httpx.AsyncClient(base_url="https://discord.com/api/v10", headers=headers, timeout=30) as client:
        response = await client.get(f"/guilds/{settings.discord_guild_id}/channels")
        response.raise_for_status()
        channels = [channel for channel in response.json() if channel.get("type") == 0]

        for old_name, updates in CHANNEL_RENAMES.items():
            channel = _find_channel(channels, old_name, updates["name"])
            if not channel:
                continue
            payload = {
                "name": updates["name"],
                "topic": updates["topic"],
            }
            renamed.append(
                {
                    "channel_id": channel["id"],
                    "old_name": channel["name"],
                    "new_name": updates["name"],
                    "topic": updates["topic"],
                }
            )
            if args.dry_run:
                continue
            patch = await client.patch(f"/channels/{channel['id']}", json=payload)
            patch.raise_for_status()

    print(json.dumps({"dry_run": args.dry_run, "renamed": renamed}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
