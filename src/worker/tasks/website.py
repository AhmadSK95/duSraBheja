"""Background task for periodic website taste refresh."""

from __future__ import annotations

from src.database import async_session
from src.services.website import refresh_website_taste


async def refresh_website_taste_task(ctx) -> dict:
    """Periodic: compare brain's current taste/focus with site and adjust."""
    async with async_session() as session:
        return await refresh_website_taste(session)
