"""Cold start: generate case studies for active projects and seed initial website sections.

Usage:
    .venv/bin/python scripts/seed_case_studies.py
"""

from __future__ import annotations

import asyncio
import logging

from src.database import async_session
from src.lib import store
from src.services.project_state import generate_case_study, recompute_project_states
from src.services.website import execute_website_change

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("seed-case-studies")


async def main():
    async with async_session() as session:
        # 1. Recompute project states
        log.info("Recomputing project states...")
        await recompute_project_states(session)

        # 2. Generate case studies for active projects (use Opus for cold start)
        snapshots = await store.list_project_state_snapshots(session, limit=6)
        for snapshot in snapshots:
            project = await store.get_note(session, snapshot.project_note_id)
            if not project:
                continue
            log.info("Generating case study for: %s", project.title)
            try:
                case = await generate_case_study(
                    session,
                    project_note_id=snapshot.project_note_id,
                    use_opus=True,
                )
                if case:
                    log.info(
                        "  Case study generated: %d decisions, %d struggles, %d learnings",
                        len(case.get("key_decisions") or []),
                        len(case.get("struggles") or []),
                        len(case.get("learnings") or []),
                    )
            except Exception:
                log.exception("  Failed to generate case study for %s", project.title)

        await session.commit()

        # 3. Seed initial website sections via the brain builder
        log.info("Seeding initial website sections...")
        try:
            result = await execute_website_change(
                session,
                "Build the initial website with sections for home, work, brain, and connect pages. "
                "Home: hero with my name, stat band with proof metrics, what-I-build text block, "
                "selected projects grid, chatbot teaser, contact strip. "
                "Work: project grid showing all projects. "
                "Brain: chat shell with starter prompts. "
                "Connect: hero, contact channels, visitor cards for hiring/freelance/curious.",
            )
            log.info("Seed result: %s", result.get("summary", "done"))
        except Exception:
            log.exception("Failed to seed website sections")

        # 4. Force-refresh public snapshots
        try:
            from src.services.public_surface import refresh_public_snapshots_if_stale

            await refresh_public_snapshots_if_stale(session, force=True)
            log.info("Public snapshots refreshed.")
        except Exception:
            log.exception("Public snapshot refresh failed (non-fatal)")

        await session.commit()
        log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
