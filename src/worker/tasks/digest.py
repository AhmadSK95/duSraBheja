"""Daily digest generation task."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import async_session
from src.lib.notifications import publish as publish_notification
from src.services.digest import generate_or_refresh_digest

EVENT_DIGEST_READY = "brain:digest_ready"


async def generate_daily_digest(
    ctx,
    *,
    trigger: str = "scheduled",
    reason: str | None = None,
    metadata: dict | None = None,
) -> dict:
    digest_date = datetime.now(ZoneInfo(settings.digest_timezone)).date()
    async with async_session() as session:
        payload = await generate_or_refresh_digest(session, digest_date=digest_date)
    await publish_notification(
        EVENT_DIGEST_READY,
        {
            "trigger": trigger,
            "reason": reason,
            "metadata": metadata or {},
            "digest_date": payload["digest_date"],
            "tasks": payload["tasks"],
            "projects": payload["projects"],
            "recent_activity": payload["recent_activity"],
            "pending_reviews": payload["pending_reviews"],
            "open_loops": payload.get("open_loops", []),
            "story_connections": payload.get("story_connections", []),
            "writing_topics": payload["writing_topics"],
        },
    )
    return payload


async def generate_scheduled_digest_tick(ctx) -> dict:
    now = datetime.now(ZoneInfo(settings.digest_timezone))
    if now.hour != settings.digest_cron_hour:
        return {"status": "skipped", "reason": f"waiting for {settings.digest_cron_hour}:00 {settings.digest_timezone}"}
    return await generate_daily_digest(ctx, trigger="scheduled")
