"""Continuous cognition task."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from src.database import async_session
from src.lib import store
from src.services.cognition import run_continuous_cognition
from src.services.library import sync_canonical_library
from src.services.project_state import generate_case_study

log = logging.getLogger("brain.cognition-task")

# Refresh case studies for active projects alongside cognition (7-day staleness gate)
CASE_STUDY_STALENESS_DAYS = 7


async def run_continuous_cognition_task(ctx) -> dict:
    async with async_session() as session:
        # C4: Sync canonical library before cognition so it operates on promoted evidence
        try:
            await sync_canonical_library(session)
            await session.commit()
        except Exception:
            log.warning("Library sync before cognition failed (non-fatal)")

        result = await run_continuous_cognition(session)

        # Refresh case studies for active projects if stale
        try:
            snapshots = await store.list_project_state_snapshots(session, limit=4)
            now = datetime.now(timezone.utc)
            for snapshot in snapshots:
                meta = dict(snapshot.metadata_ or {})
                last_case = meta.get("case_study_refreshed_at")
                if last_case:
                    try:
                        last_dt = datetime.fromisoformat(str(last_case))
                        if now - last_dt < timedelta(days=CASE_STUDY_STALENESS_DAYS):
                            continue
                    except (ValueError, TypeError):
                        pass

                case = await generate_case_study(session, project_note_id=snapshot.project_note_id)
                if case:
                    meta["case_study_refreshed_at"] = now.isoformat()
                    snapshot.metadata_ = meta
                    await session.flush()
                    log.info("Refreshed case study for project %s", snapshot.project_note_id)

            await session.commit()
        except Exception:
            log.exception("Case study refresh failed (non-fatal)")

        return result
