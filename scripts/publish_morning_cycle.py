#!/usr/bin/env python3
"""Generate and post a fresh daily board and daily digest."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from src.bot.cogs.inbox import build_board_embed, build_digest_embeds
from src.config import settings
from src.database import async_session
from src.services.boards import daily_board_window, generate_or_refresh_board
from src.services.digest import generate_or_refresh_digest


def _today_local() -> date:
    return datetime.now(ZoneInfo(settings.digest_timezone)).date()


async def _channel_map(client: httpx.AsyncClient) -> dict[str, dict]:
    response = await client.get(f"/guilds/{settings.discord_guild_id}/channels")
    response.raise_for_status()
    return {channel["name"]: channel for channel in response.json() if channel.get("type") == 0}


async def _post_embed(
    client: httpx.AsyncClient,
    *,
    channels: dict[str, dict],
    primary_name: str,
    fallback_names: tuple[str, ...],
    embed,
) -> dict:
    channel = channels.get(primary_name)
    if channel is None:
        for fallback in fallback_names:
            channel = channels.get(fallback)
            if channel:
                break
    if channel is None:
        raise RuntimeError(f"Discord channel not found: {primary_name}")

    response = await client.post(
        f"/channels/{channel['id']}/messages",
        json={"embeds": [embed.to_dict()]},
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "channel_id": channel["id"],
        "channel_name": channel["name"],
        "message_id": payload["id"],
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a fresh daily board and daily digest to Discord.")
    parser.add_argument(
        "--digest-date",
        default=None,
        help="Digest date in YYYY-MM-DD. Defaults to today in the configured local timezone.",
    )
    args = parser.parse_args()

    digest_date = date.fromisoformat(args.digest_date) if args.digest_date else _today_local()
    board_date = digest_date - timedelta(days=1)

    async with async_session() as session:
        board_payload = await generate_or_refresh_board(session, window=daily_board_window(board_date))
        digest_payload = await generate_or_refresh_digest(session, digest_date=digest_date, trigger="manual")

    headers = {
        "Authorization": f"Bot {settings.discord_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(base_url="https://discord.com/api/v10", headers=headers, timeout=30) as client:
        channels = await _channel_map(client)
        board_post = await _post_embed(
            client,
            channels=channels,
            primary_name=settings.daily_board_channel_name,
            fallback_names=("daily-planner",),
            embed=build_board_embed(board_payload),
        )
        digest_posts = []
        for embed in build_digest_embeds(digest_payload):
            digest_posts.append(
                await _post_embed(
                    client,
                    channels=channels,
                    primary_name=settings.daily_digest_channel_name,
                    fallback_names=(),
                    embed=embed,
                )
            )

    print(
        json.dumps(
            {
                "digest_date": digest_date.isoformat(),
                "board_date": board_date.isoformat(),
                "board_post": board_post,
                "digest_posts": digest_posts,
                "board_summary": board_payload.get("summary"),
                "digest_summary": digest_payload.get("summary"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
