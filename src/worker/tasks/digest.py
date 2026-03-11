"""Daily digest generation task."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import async_session
from src.services.digest import generate_or_refresh_digest


async def generate_daily_digest(ctx) -> dict:
    digest_date = datetime.now(ZoneInfo(settings.digest_timezone)).date()
    async with async_session() as session:
        payload = await generate_or_refresh_digest(session, digest_date=digest_date)
    return payload
