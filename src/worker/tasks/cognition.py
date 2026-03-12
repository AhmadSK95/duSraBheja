"""Continuous cognition task."""

from __future__ import annotations

from src.database import async_session
from src.services.cognition import run_continuous_cognition


async def run_continuous_cognition_task(ctx) -> dict:
    async with async_session() as session:
        return await run_continuous_cognition(session)
