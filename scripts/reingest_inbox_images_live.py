#!/usr/bin/env python3
"""Reprocess Discord inbox images directly against the live brain database."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from src.agents.classifier import classify
from src.config import settings
from src.database import async_session
from src.lib.store import (
    create_artifact,
    create_classification,
    get_artifact_by_discord_id,
    reset_artifact_processing,
    update_artifact,
)
from src.services.project_state import recompute_project_states
from src.worker.extractors.router import extract
from src.worker.tasks.clarify import ask_clarification
from src.worker.tasks.embed import generate_embeddings
from src.worker.tasks.ingest import _content_type_to_category, _download_attachment
import src.worker.tasks.librarian as librarian_task

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp"}


def _is_image_attachment(attachment: dict[str, Any]) -> bool:
    content_type = (attachment.get("content_type") or "").lower()
    filename = (attachment.get("filename") or "").lower()
    return content_type.startswith("image/") or any(filename.endswith(suffix) for suffix in IMAGE_SUFFIXES)


def _normalize_attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "url": attachment["url"],
            "filename": attachment.get("filename"),
            "content_type": attachment.get("content_type") or "application/octet-stream",
            "size": attachment.get("size"),
        }
        for attachment in message.get("attachments", [])
        if _is_image_attachment(attachment)
    ]


def _parse_discord_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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


async def _extract_message_payload(
    *,
    session,
    message: dict[str, Any],
    attachments: list[dict[str, Any]],
    existing_blob_ref: str | None = None,
) -> dict[str, Any]:
    extracted_texts: list[str] = []
    blob_ref = existing_blob_ref
    blob_hash = None
    blob_mime = None
    blob_size = None
    content_type = "image"
    attachment_records: list[dict[str, Any]] = []

    for attachment in attachments:
        file_path = None
        meta: dict[str, Any] = {}
        if len(attachments) == 1 and existing_blob_ref and os.path.exists(existing_blob_ref):
            file_path = existing_blob_ref
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

        attachment_records.append(
            {
                "filename": attachment.get("filename"),
                "content_type": attachment.get("content_type"),
                "size": attachment.get("size"),
                "blob_ref": blob_ref,
                "blob_hash": blob_hash,
            }
        )
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
        "attachment_records": attachment_records,
    }


async def _process_artifact_inline(artifact_id: uuid.UUID) -> str:
    async with async_session() as session:
        from src.lib.store import get_artifact

        artifact = await get_artifact(session, artifact_id)
        if not artifact or not artifact.raw_text:
            return "skipped"
        result = await classify(session, artifact.raw_text, content_type=artifact.content_type)
        meta = result.pop("_meta", {})
        is_final = (
            result["confidence"] >= settings.confidence_threshold
            and result.get("validation_status", "validated") == "validated"
        )
        classification = await create_classification(
            session,
            artifact_id=artifact_id,
            category=result["category"],
            confidence=result["confidence"],
            capture_intent=result.get("capture_intent"),
            intent_confidence=result.get("intent_confidence"),
            entities=result.get("entities", []),
            tags=result.get("tags", []),
            priority=result.get("priority", "medium"),
            suggested_action=result.get("suggested_action"),
            validation_status=result.get("validation_status", "validated"),
            quality_issues=result.get("quality_issues", []),
            eligible_for_boards=result.get("eligible_for_boards", True),
            eligible_for_project_state=result.get("eligible_for_project_state", True),
            model_used=meta.get("model", "unknown"),
            tokens_used=meta.get("tokens_used"),
            cost_usd=meta.get("cost_usd"),
            is_final=is_final,
        )
        artifact.summary = result.get("summary", artifact.raw_text[:200])
        await session.commit()

    if is_final:
        await generate_embeddings(None, artifact_id=str(artifact_id))
        await librarian_task.process_librarian(None, artifact_id=str(artifact_id), classification_id=str(classification.id))
        return "validated"

    await ask_clarification(None, artifact_id=str(artifact_id), classification_id=str(classification.id))
    return "needs_review"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Reprocess inbox images directly against the brain database.")
    parser.add_argument("--channel", default=settings.inbox_channel_name, help="Discord source channel name")
    parser.add_argument("--limit", type=int, default=0, help="Stop after this many image messages (0 = no limit)")
    parser.add_argument("--skip-existing", action="store_true", help="Only ingest images that do not already exist.")
    args = parser.parse_args()

    async def _noop_publish_event(*_args, **_kwargs) -> None:
        return None

    librarian_task.publish_event = _noop_publish_event
    settings.blob_storage_path = "/tmp/duSraBheja_blobs"
    os.makedirs(settings.blob_storage_path, exist_ok=True)

    headers = {
        "Authorization": f"Bot {settings.discord_token}",
        "Content-Type": "application/json",
    }
    stats = {
        "channel": args.channel,
        "image_messages_seen": 0,
        "reprocessed_existing": 0,
        "created_new": 0,
        "skipped_existing": 0,
        "validated": 0,
        "needs_review": 0,
        "skipped": 0,
        "failed": 0,
    }

    async with httpx.AsyncClient(base_url="https://discord.com/api/v10", headers=headers, timeout=60) as client:
        channel = await _get_channel_by_name(client, args.channel)
        async for message in _iter_messages(client, channel["id"]):
            if message.get("author", {}).get("bot"):
                continue
            attachments = _normalize_attachments(message)
            if not attachments:
                continue
            stats["image_messages_seen"] += 1

            try:
                async with async_session() as session:
                    existing = await get_artifact_by_discord_id(session, message["id"])
                    if existing and args.skip_existing:
                        stats["skipped_existing"] += 1
                        continue

                    extracted_payload = await _extract_message_payload(
                        session=session,
                        message=message,
                        attachments=attachments,
                        existing_blob_ref=existing.blob_ref if existing else None,
                    )
                    metadata = {
                        "attachments": extracted_payload["attachment_records"],
                        "reingested_from_discord": True,
                    }
                    if existing:
                        await reset_artifact_processing(session, existing.id)
                        artifact = await update_artifact(
                            session,
                            existing.id,
                            content_type=extracted_payload["content_type"],
                            raw_text=extracted_payload["raw_text"],
                            summary=None,
                            blob_ref=extracted_payload["blob_ref"],
                            blob_hash=extracted_payload["blob_hash"],
                            blob_mime=extracted_payload["blob_mime"],
                            blob_size_bytes=extracted_payload["blob_size_bytes"],
                            metadata_=metadata,
                        )
                        artifact_id = artifact.id
                        stats["reprocessed_existing"] += 1
                    else:
                        created_at = _parse_discord_timestamp(message.get("timestamp"))
                        artifact = await create_artifact(
                            session,
                            discord_message_id=message["id"],
                            discord_channel_id=channel["id"],
                            content_type=extracted_payload["content_type"],
                            raw_text=extracted_payload["raw_text"],
                            blob_ref=extracted_payload["blob_ref"],
                            blob_hash=extracted_payload["blob_hash"],
                            blob_mime=extracted_payload["blob_mime"],
                            blob_size_bytes=extracted_payload["blob_size_bytes"],
                            metadata_=metadata,
                            source="discord",
                            created_at=created_at,
                            updated_at=created_at or datetime.now(timezone.utc),
                        )
                        artifact_id = artifact.id
                        stats["created_new"] += 1

                outcome = await _process_artifact_inline(artifact_id)
                stats[outcome] += 1
            except Exception as exc:
                stats["failed"] += 1
                print(json.dumps({"message_id": message["id"], "error": str(exc)}, indent=2))

            if args.limit and stats["image_messages_seen"] >= args.limit:
                break

    async with async_session() as session:
        await recompute_project_states(session)

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
