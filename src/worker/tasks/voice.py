"""Voice profile refresh task."""

from __future__ import annotations

from src.database import async_session
from src.services.voice import refresh_voice_profile


async def refresh_voice_profile_task(ctx) -> dict:
    async with async_session() as session:
        return await refresh_voice_profile(session)
