"""Daily digest generation task."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

from src.config import settings
from src.database import async_session
from src.lib.notifications import publish as publish_notification
from src.worker.tasks.boards import generate_daily_board, generate_weekly_board
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
            "summary": payload.get("summary"),
            "board_date": payload.get("board_date"),
            "project_status": payload.get("project_status", []),
            "possible_tasks": payload.get("possible_tasks", []),
            "reminders_due_today": payload.get("reminders_due_today", []),
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
        await generate_daily_board(ctx, run_date=now.date().isoformat())
        if now.weekday() == settings.weekly_board_cron_weekday:
            await generate_weekly_board(ctx, run_date=now.date().isoformat())
        return await generate_daily_digest(ctx, trigger="scheduled")
    except Exception:
        if claimed:
            await redis.delete(marker_key)
        raise
    finally:
        await redis.aclose()
