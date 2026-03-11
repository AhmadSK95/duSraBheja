#!/usr/bin/env python3
"""Clean bot planner output and reprocess Discord inbox planner images."""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

import httpx

from src.config import settings
from src.database import async_session
from src.lib.store import (
    get_artifact_by_discord_id,
    reset_artifact_processing,
    update_artifact,
)
from src.worker.extractors.router import extract
from src.worker.main import enqueue_classify, enqueue_ingest
from src.worker.tasks.ingest import _content_type_to_category, _download_attachment

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"}


def _is_image_attachment(attachment: dict[str, Any]) -> bool:
    content_type = (attachment.get("content_type") or "").lower()
    filename = (attachment.get("filename") or "").lower()
    return content_type.startswith("image/") or any(filename.endswith(suffix) for suffix in IMAGE_SUFFIXES)


async def _get_text_channels(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    response = await client.get(f"/guilds/{settings.discord_guild_id}/channels")
    response.raise_for_status()
    return {channel["name"]: channel for channel in response.json() if channel.get("type") == 0}


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


async def _delete_message(client: httpx.AsyncClient, channel_id: str, message_id: str) -> None:
    response = await client.delete(f"/channels/{channel_id}/messages/{message_id}")
    response.raise_for_status()


async def _purge_bot_outputs(
    client: httpx.AsyncClient,
    *,
    bot_user_id: str,
    inbox_channel_id: str,
    planner_channel_ids: list[str],
) -> dict[str, int]:
    deleted_inbox = 0
    deleted_planner = 0

    async for message in _iter_messages(client, inbox_channel_id):
        if message.get("author", {}).get("id") != bot_user_id:
            continue
        if not message.get("message_reference"):
            continue
        await _delete_message(client, inbox_channel_id, message["id"])
        deleted_inbox += 1

    for channel_id in planner_channel_ids:
        async for message in _iter_messages(client, channel_id):
            if message.get("author", {}).get("id") != bot_user_id:
                continue
            if not message.get("embeds"):
                continue
            await _delete_message(client, channel_id, message["id"])
            deleted_planner += 1

    return {
        "deleted_inbox_receipts": deleted_inbox,
        "deleted_planner_cards": deleted_planner,
    }


async def _reextract_artifact_text(
    session,
    *,
    message: dict[str, Any],
    attachments: list[dict[str, Any]],
    existing_artifact,
) -> dict[str, Any]:
    extracted_texts: list[str] = []
    blob_ref = existing_artifact.blob_ref
    blob_hash = existing_artifact.blob_hash
    blob_mime = existing_artifact.blob_mime
    blob_size = existing_artifact.blob_size_bytes
    content_type = existing_artifact.content_type or "image"

    for attachment in attachments:
        file_path = None
        meta: dict[str, Any] = {}
        if len(attachments) == 1 and existing_artifact.blob_ref and os.path.exists(existing_artifact.blob_ref):
            file_path = existing_artifact.blob_ref
        else:
            file_path, meta = await _download_attachment(attachment)
            if file_path:
                blob_ref = meta["blob_ref"]
                blob_hash = meta["blob_hash"]
                blob_mime = attachment.get("content_type") or "application/octet-stream"
                blob_size = attachment.get("size")
                content_type = _content_type_to_category(blob_mime)

        if not file_path:
            continue

        extracted = await extract(file_path, attachment.get("content_type") or "image/png", session=session)
        if extracted:
            extracted_texts.append(extracted)

    raw_text = message.get("content") or ""
    if extracted_texts:
        raw_text = raw_text + "\n\n" + "\n\n".join(extracted_texts) if raw_text else "\n\n".join(extracted_texts)

    return {
        "content_type": content_type,
        "raw_text": raw_text,
        "blob_ref": blob_ref,
        "blob_hash": blob_hash,
        "blob_mime": blob_mime,
        "blob_size_bytes": blob_size,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Clean and reprocess Discord planner images")
    parser.add_argument("--channel", default=settings.inbox_channel_name, help="Discord source channel name")
    parser.add_argument("--limit", type=int, default=0, help="Stop after this many image messages (0 = no limit)")
    parser.add_argument(
        "--purge-bot-output",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete existing bot planner cards and inbox receipts before replay",
    )
    args = parser.parse_args()

    headers = {
        "Authorization": f"Bot {settings.discord_token}",
        "Content-Type": "application/json",
    }

    purged = {"deleted_inbox_receipts": 0, "deleted_planner_cards": 0}
    reprocessed = 0
    queued_new = 0
    skipped_non_image = 0

    async with httpx.AsyncClient(base_url="https://discord.com/api/v10", headers=headers, timeout=30) as client:
        me_response = await client.get("/users/@me")
        me_response.raise_for_status()
        bot_user_id = me_response.json()["id"]

        channels = await _get_text_channels(client)
        source_channel = channels[args.channel]
        planner_channels = [channels[name]["id"] for name in ("daily-planner", "weekly-planner") if name in channels]

        if args.purge_bot_output:
            purged = await _purge_bot_outputs(
                client,
                bot_user_id=bot_user_id,
                inbox_channel_id=source_channel["id"],
                planner_channel_ids=planner_channels,
            )

        async for message in _iter_messages(client, source_channel["id"]):
            if message.get("author", {}).get("bot"):
                continue

            attachments = [attachment for attachment in message.get("attachments", []) if _is_image_attachment(attachment)]
            if not attachments:
                skipped_non_image += 1
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

            async with async_session() as session:
                existing = await get_artifact_by_discord_id(session, message["id"])
                if existing:
                    extracted_payload = await _reextract_artifact_text(
                        session,
                        message=message,
                        attachments=normalized_attachments,
                        existing_artifact=existing,
                    )
                    await reset_artifact_processing(session, existing.id)
                    await update_artifact(
                        session,
                        existing.id,
                        content_type=extracted_payload["content_type"],
                        raw_text=extracted_payload["raw_text"],
                        summary=None,
                        blob_ref=extracted_payload["blob_ref"],
                        blob_hash=extracted_payload["blob_hash"],
                        blob_mime=extracted_payload["blob_mime"],
                        blob_size_bytes=extracted_payload["blob_size_bytes"],
                    )
                    await enqueue_classify(str(existing.id))
                    reprocessed += 1
                else:
                    await enqueue_ingest(
                        discord_message_id=message["id"],
                        discord_channel_id=source_channel["id"],
                        text=message.get("content") or "",
                        attachments=normalized_attachments,
                    )
                    queued_new += 1

            if args.limit and reprocessed + queued_new >= args.limit:
                break

    print(
        {
            **purged,
            "reprocessed_existing": reprocessed,
            "queued_new": queued_new,
            "skipped_non_image": skipped_non_image,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
