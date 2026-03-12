"""Periodic knowledge-base refresh task."""

from __future__ import annotations

from src.config import settings
from src.database import async_session
from src.services.knowledge import refresh_knowledge_base


async def generate_knowledge_refresh(ctx) -> dict:
    async with async_session() as session:
        return await refresh_knowledge_base(session, limit=settings.knowledge_max_projects_per_run)
