"""Daily digest generation task."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

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
        payload = await generate_or_refresh_digest(session, digest_date=digest_date, trigger=trigger)
    await publish_notification(
        EVENT_DIGEST_READY,
        {
            "trigger": trigger,
            "reason": reason,
            "metadata": metadata or {},
            "digest_date": payload["digest_date"],
            "headline": payload.get("headline"),
            "narrative": payload.get("narrative"),
            "tasks": payload["tasks"],
            "recommended_tasks": payload.get("recommended_tasks", []),
            "best_ideas": payload.get("best_ideas", []),
            "projects": payload["projects"],
            "project_assessments": payload.get("project_assessments", []),
            "recent_activity": payload["recent_activity"],
            "pending_reviews": payload["pending_reviews"],
            "open_loops": payload.get("open_loops", []),
            "story_connections": payload.get("story_connections", []),
            "writing_topics": payload["writing_topics"],
            "writing_topic_items": payload.get("writing_topic_items", []),
            "video_recommendations": payload.get("video_recommendations", []),
            "brain_teasers": payload.get("brain_teasers", []),
            "reminders_due_today": payload.get("reminders_due_today", []),
            "improvement_focus": payload.get("improvement_focus", []),
            "low_confidence_sections": payload.get("low_confidence_sections", []),
        },
    )
    return payload


async def generate_scheduled_digest_tick(ctx) -> dict:
    now = datetime.now(ZoneInfo(settings.digest_timezone))
    if now.hour < settings.digest_cron_hour:
        return {"status": "skipped", "reason": f"waiting for {settings.digest_cron_hour}:00 {settings.digest_timezone}"}

    redis = Redis.from_url(settings.redis_url)
    marker_key = f"brain:digest:scheduled:{now.date().isoformat()}"
    claimed = False
    try:
        claimed = bool(await redis.set(marker_key, "1", ex=60 * 60 * 36, nx=True))
        if not claimed:
            return {"status": "skipped", "reason": "scheduled digest already published"}
        return await generate_daily_digest(ctx, trigger="scheduled")
    except Exception:
        if claimed:
            await redis.delete(marker_key)
        raise
    finally:
        await redis.aclose()
