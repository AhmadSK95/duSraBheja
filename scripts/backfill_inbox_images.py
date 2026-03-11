#!/usr/bin/env python3
"""Backfill Discord image messages into the brain queue."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import httpx

from src.config import settings
from src.database import async_session
from src.lib.store import get_artifact_by_discord_id
from src.worker.main import enqueue_ingest

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"}


def _is_image_attachment(attachment: dict[str, Any]) -> bool:
    content_type = (attachment.get("content_type") or "").lower()
    filename = (attachment.get("filename") or "").lower()
    return content_type.startswith("image/") or any(filename.endswith(suffix) for suffix in IMAGE_SUFFIXES)


async def _get_channel_by_name(client: httpx.AsyncClient, channel_name: str) -> dict[str, Any]:
    response = await client.get(f"/guilds/{settings.discord_guild_id}/channels")
    response.raise_for_status()
    channels = response.json()
    for channel in channels:
        if channel.get("name") == channel_name:
            return channel
    raise RuntimeError(f"Discord channel not found: {channel_name}")


async def _iter_messages(client: httpx.AsyncClient, channel_id: str):
    before: str | None = None
    while True:
        params = {"limit": 100}
        if before:
            params["before"] = before
        response = await client.get(f"/channels/{channel_id}/messages", params=params)
        response.raise_for_status()
        messages = response.json()
        if not messages:
            return
        for message in messages:
            yield message
        before = messages[-1]["id"]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Discord image messages into the brain queue")
    parser.add_argument("--channel", default=settings.inbox_channel_name, help="Discord channel name")
    parser.add_argument("--images-only", action="store_true", default=True)
    parser.add_argument("--force", action="store_true", help="Requeue even if already ingested")
    parser.add_argument("--limit", type=int, default=0, help="Stop after this many queued messages (0 = no limit)")
    args = parser.parse_args()

    queued = 0
    skipped_existing = 0
    skipped_non_image = 0

    headers = {
        "Authorization": f"Bot {settings.discord_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(base_url="https://discord.com/api/v10", headers=headers, timeout=30) as client:
        channel = await _get_channel_by_name(client, args.channel)
        async for message in _iter_messages(client, channel["id"]):
            if message.get("author", {}).get("bot"):
                continue

            attachments = message.get("attachments", [])
            if args.images_only:
                attachments = [attachment for attachment in attachments if _is_image_attachment(attachment)]
                if not attachments:
                    skipped_non_image += 1
                    continue

            async with async_session() as session:
                existing = await get_artifact_by_discord_id(session, message["id"])
            if existing and not args.force:
                skipped_existing += 1
                continue

            normalized_attachments = [
                {
                    "url": attachment["url"],
                    "filename": attachment.get("filename"),
                    "content_type": attachment.get("content_type") or "application/octet-stream",
                    "size": attachment.get("size"),
                }
                for attachment in attachments
            ]
            await enqueue_ingest(
                discord_message_id=message["id"],
                discord_channel_id=channel["id"],
                text=message.get("content") or "",
                attachments=normalized_attachments,
            )
            queued += 1
            if args.limit and queued >= args.limit:
                break

    print(
        {
            "channel": args.channel,
            "queued": queued,
            "skipped_existing": skipped_existing,
            "skipped_non_image": skipped_non_image,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
