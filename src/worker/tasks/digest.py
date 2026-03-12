"""Daily digest generation task."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import async_session
from src.services.digest import generate_or_refresh_digest
from src.worker.main import EVENT_DIGEST_READY, publish_event


async def generate_daily_digest(ctx) -> dict:
    digest_date = datetime.now(ZoneInfo(settings.digest_timezone)).date()
    async with async_session() as session:
        payload = await generate_or_refresh_digest(session, digest_date=digest_date)
    await publish_event(
        EVENT_DIGEST_READY,
        {
            "digest_date": payload["digest_date"],
            "tasks": payload["tasks"],
            "projects": payload["projects"],
            "recent_activity": payload["recent_activity"],
            "pending_reviews": payload["pending_reviews"],
            "writing_topics": payload["writing_topics"],
        },
    )
    return payload
