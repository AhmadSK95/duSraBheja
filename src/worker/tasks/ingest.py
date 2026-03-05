"""Ingest task — download attachments, extract text, create artifact, enqueue classification."""

import hashlib
import logging
import os
import uuid
from datetime import datetime

import httpx

from src.config import settings
from src.database import async_session
from src.lib.store import create_artifact
from src.worker.extractors.router import extract

log = logging.getLogger("brain-worker.ingest")


def _content_type_to_category(mime: str) -> str:
    """Map MIME type to content_type field value."""
    mime = mime.split(";")[0].strip().lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime == "application/pdf":
        return "pdf"
    if "spreadsheet" in mime or "excel" in mime:
        return "excel"
    return "text"


async def process_inbox_message(
    ctx,
    discord_message_id: str | None,
    discord_channel_id: str,
    text: str,
    attachments: list[dict],
    force_category: str | None = None,
    source: str = "discord",
):
    """Main ingestion pipeline — called by ARQ worker."""
    log.info(f"Processing message {discord_message_id} with {len(attachments)} attachments")

    async with async_session() as session:
        extracted_texts = []
        blob_ref = None
        blob_hash = None
        blob_mime = None
        blob_size = None
        content_type = "text"

        # Download and extract attachments
        for att in attachments:
            file_path, meta = await _download_attachment(att)
            if file_path:
                blob_ref = meta["blob_ref"]
                blob_hash = meta["blob_hash"]
                blob_mime = att["content_type"]
                blob_size = att.get("size")
                content_type = _content_type_to_category(att["content_type"])

                extracted = await extract(file_path, att["content_type"], session=session)
                if extracted:
                    extracted_texts.append(extracted)

        # Combine text
        raw_text = text or ""
        if extracted_texts:
            raw_text = raw_text + "\n\n" + "\n\n".join(extracted_texts) if raw_text else "\n\n".join(extracted_texts)

        if not raw_text.strip():
            log.warning(f"No text extracted for message {discord_message_id}")
            return

        # Create artifact
        artifact = await create_artifact(
            session,
            discord_message_id=discord_message_id,
            discord_channel_id=discord_channel_id,
            content_type=content_type,
            raw_text=raw_text,
            blob_ref=blob_ref,
            blob_hash=blob_hash,
            blob_mime=blob_mime,
            blob_size_bytes=blob_size,
            source=source,
        )

        log.info(f"Created artifact {artifact.id} (type={content_type})")

        # Enqueue classification
        from src.worker.main import get_pool

        pool = await get_pool()
        await pool.enqueue_job(
            "classify_artifact",
            artifact_id=str(artifact.id),
            force_category=force_category,
        )


async def _download_attachment(att: dict) -> tuple[str | None, dict]:
    """Download a Discord attachment to blob storage. Returns (file_path, metadata)."""
    url = att["url"]
    filename = att.get("filename", "unknown")

    # Create storage directory
    now = datetime.utcnow()
    dir_path = os.path.join(settings.blob_storage_path, now.strftime("%Y-%m"))
    os.makedirs(dir_path, exist_ok=True)

    # Generate unique filename
    ext = os.path.splitext(filename)[1] or ".bin"
    file_id = str(uuid.uuid4())
    file_path = os.path.join(dir_path, f"{file_id}{ext}")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        data = resp.content
        with open(file_path, "wb") as f:
            f.write(data)

        sha256 = hashlib.sha256(data).hexdigest()

        return file_path, {
            "blob_ref": file_path,
            "blob_hash": sha256,
            "filename": filename,
        }

    except Exception as e:
        log.error(f"Failed to download attachment {url}: {e}")
        return None, {}
