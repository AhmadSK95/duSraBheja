"""Board generation tasks."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import async_session
from src.lib.notifications import publish as publish_notification
from src.services.boards import (
    generate_or_refresh_board,
    previous_daily_board_window,
    previous_weekly_board_window,
)

EVENT_BOARD_READY = "brain:board_ready"


async def generate_daily_board(ctx, *, run_date: str | None = None) -> dict:
    local_date = (
        datetime.fromisoformat(run_date).date()
        if run_date
        else datetime.now(ZoneInfo(settings.digest_timezone)).date()
    )
    window = previous_daily_board_window(local_date)
    async with async_session() as session:
        payload = await generate_or_refresh_board(session, window=window)
    await publish_notification(
        EVENT_BOARD_READY,
        {
            **payload,
            "channel_name": settings.daily_board_channel_name,
        },
    )
    return payload


async def generate_weekly_board(ctx, *, run_date: str | None = None) -> dict:
    local_date = (
        datetime.fromisoformat(run_date).date()
        if run_date
        else datetime.now(ZoneInfo(settings.digest_timezone)).date()
    )
    window = previous_weekly_board_window(local_date)
    async with async_session() as session:
        payload = await generate_or_refresh_board(session, window=window)
    await publish_notification(
        EVENT_BOARD_READY,
        {
            **payload,
            "channel_name": settings.weekly_board_channel_name,
        },
    )
    return payload
