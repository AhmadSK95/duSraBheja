"""Public surface refresh and autonomous improvement-cycle tasks."""

from __future__ import annotations

from src.database import async_session
from src.services.public_surface import run_product_improvement_cycle, run_public_surface_refresh
from src.worker.main import (
    EVENT_IMPROVEMENT_CYCLE_COMPLETED,
    EVENT_PUBLIC_SURFACE_REFRESH_COMPLETED,
    EVENT_PUBLIC_SURFACE_REVIEW_CREATED,
    publish_event,
)


async def refresh_public_surface_task(ctx) -> dict:
    async with async_session() as session:
        result = await run_public_surface_refresh(session, trigger="scheduled", force=True)
    await publish_event(EVENT_PUBLIC_SURFACE_REFRESH_COMPLETED, result)
    return result


async def run_product_improvement_cycle_task(ctx) -> dict:
    async with async_session() as session:
        result = await run_product_improvement_cycle(session, trigger="scheduled")
    await publish_event(EVENT_IMPROVEMENT_CYCLE_COMPLETED, result)
    if result.get("review_id"):
        await publish_event(
            EVENT_PUBLIC_SURFACE_REVIEW_CREATED,
            {
                "review_id": result.get("review_id"),
                "review_key": result.get("review_key"),
                "subject_type": result.get("review_subject_type"),
                "subject_slug": result.get("review_subject_slug"),
                "diff_summary": result.get("review_diff_summary"),
                "cycle_id": result.get("cycle_id"),
                "cycle_number": result.get("cycle_number"),
            },
        )
    return result
