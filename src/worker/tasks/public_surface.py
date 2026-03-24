"""Public surface refresh and autonomous improvement-cycle tasks."""

from __future__ import annotations

from src.database import async_session
from src.services.public_surface import run_product_improvement_cycle, run_public_surface_refresh

DISCORD_REVIEW_SUBJECT_TYPES = {"home", "about", "brain", "work", "project", "public-content"}


async def refresh_public_surface_task(ctx) -> dict:
    from src.worker.main import (
        EVENT_PUBLIC_SURFACE_REFRESH_COMPLETED,
        EVENT_PUBLIC_SURFACE_REVIEW_CREATED,
        publish_event,
    )

    async with async_session() as session:
        result = await run_public_surface_refresh(session, trigger="scheduled", force=True)
    await publish_event(EVENT_PUBLIC_SURFACE_REFRESH_COMPLETED, result)
    for review in list(result.get("staged_reviews") or []):
        if review.get("subject_type") not in DISCORD_REVIEW_SUBJECT_TYPES:
            continue
        await publish_event(
            EVENT_PUBLIC_SURFACE_REVIEW_CREATED,
            {
                "review_id": review.get("review_id"),
                "review_key": review.get("review_key"),
                "subject_type": review.get("subject_type"),
                "subject_slug": review.get("subject_slug"),
                "diff_summary": review.get("diff_summary"),
            },
        )
    return result


async def run_product_improvement_cycle_task(ctx) -> dict:
    from src.worker.main import (
        EVENT_IMPROVEMENT_CYCLE_COMPLETED,
        EVENT_PUBLIC_SURFACE_REVIEW_CREATED,
        publish_event,
    )

    async with async_session() as session:
        result = await run_product_improvement_cycle(session, trigger="scheduled")
    await publish_event(EVENT_IMPROVEMENT_CYCLE_COMPLETED, result)
    if result.get("review_id") and result.get("review_subject_type") in DISCORD_REVIEW_SUBJECT_TYPES:
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
