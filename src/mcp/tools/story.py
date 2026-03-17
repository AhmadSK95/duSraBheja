"""MCP tools for project story access and agent publication."""

from mcp.server.fastmcp import FastMCP

from src.constants import PROJECT_MANUAL_STATES
from src.database import async_session
from src.lib import store
from src.lib.time import human_datetime_payload
from src.services.identity import resolve_project
from src.services.project_state import recompute_project_states
from src.services.query import build_active_projects_overview, query_brain
from src.services.reminders import store_reminder
from src.services.session_bootstrap import (
    build_session_bootstrap,
    publish_curated_session_story,
    record_session_closeout,
)
from src.services.story import (
    build_project_latest_closeout_payload,
    build_project_story_payload,
    publish_story_entry,
)


def register(mcp: FastMCP):
    @mcp.tool()
    async def publish_progress(
        actor_name: str,
        project_ref: str,
        title: str,
        body_markdown: str,
        entry_type: str = "progress_update",
        tags: list[str] | None = None,
        source_links: list[str] | None = None,
    ) -> dict:
        """Publish agent or human progress into the shared project story."""
        async with async_session() as session:
            result = await publish_story_entry(
                session,
                actor_type="agent",
                actor_name=actor_name,
                entry_type=entry_type,
                title=title,
                body_markdown=body_markdown,
                project_ref=project_ref,
                tags=tags or [],
                source_links=source_links or [],
                source="mcp",
                category="project",
            )

        return {
            "journal_entry_id": str(result["journal_entry"].id),
            "project_id": str(result["project_note"].id) if result["project_note"] else None,
            "status": "stored",
        }

    @mcp.tool()
    async def publish_context_dump(
        actor_name: str,
        project_ref: str,
        title: str,
        body_markdown: str,
        tags: list[str] | None = None,
    ) -> dict:
        """Publish a larger context dump into the project story."""
        async with async_session() as session:
            result = await publish_story_entry(
                session,
                actor_type="agent",
                actor_name=actor_name,
                entry_type="context_dump",
                title=title,
                body_markdown=body_markdown,
                project_ref=project_ref,
                tags=tags or [],
                source="mcp",
                category="project",
            )

        return {
            "journal_entry_id": str(result["journal_entry"].id),
            "status": "stored",
        }

    @mcp.tool()
    async def get_project_story(project_name: str) -> dict:
        """Return the canonical story payload for a project."""
        async with async_session() as session:
            project = await resolve_project(
                session,
                project_hint=project_name,
                source_refs=[project_name],
                create_if_missing=False,
            )
            if not project:
                return {"error": f"Project '{project_name}' not found"}
            return await build_project_story_payload(session, project.id)

    @mcp.tool()
    async def get_latest_project_closeout(project_name: str) -> dict:
        """Return the latest session closeout for a project and whether newer activity exists."""
        async with async_session() as session:
            project = await resolve_project(
                session,
                project_hint=project_name,
                source_refs=[project_name],
                create_if_missing=False,
            )
            if not project:
                return {"error": f"Project '{project_name}' not found"}
            return await build_project_latest_closeout_payload(session, project.id)

    @mcp.tool()
    async def list_recent_activity(limit: int = 20) -> list[dict]:
        """List recent human and agent activity across the brain."""
        async with async_session() as session:
            entries = await store.list_recent_activity(session, limit=limit)
            return [
                {
                    "id": str(entry.id),
                    "title": entry.title,
                    "entry_type": entry.entry_type,
                    "actor_type": entry.actor_type,
                    "actor_name": entry.actor_name,
                    "summary": entry.summary,
                    "happened_at": str(entry.happened_at),
                }
                for entry in entries
            ]

    @mcp.tool()
    async def query_brain_mode(question: str, mode: str = "answer", deep: bool = False) -> dict:
        """Query the brain in answer/active_projects/latest/timeline/changed_since/sources/project_review mode."""
        async with async_session() as session:
            return await query_brain(session, question=question, mode=mode, use_opus=deep)

    @mcp.tool()
    async def list_active_projects(limit: int = 6) -> dict:
        """Return the current active-project board grounded in stored project snapshots."""
        async with async_session() as session:
            projects = await build_active_projects_overview(session, limit=limit)
            return {
                "projects": projects,
                "count": len(projects),
            }

    @mcp.tool()
    async def bootstrap_session(
        agent_kind: str,
        session_id: str,
        cwd: str | None = None,
        project_hint: str | None = None,
        task_hint: str | None = None,
        include_web: bool = True,
    ) -> dict:
        """Build the mandatory reboot brief for owned agents at session start."""
        async with async_session() as session:
            return await build_session_bootstrap(
                session,
                agent_kind=agent_kind,
                session_id=session_id,
                cwd=cwd,
                project_hint=project_hint,
                task_hint=task_hint,
                include_web=include_web,
            )

    @mcp.tool()
    async def publish_session_closeout(
        agent_kind: str,
        session_id: str,
        summary: str,
        cwd: str | None = None,
        project_ref: str | None = None,
        decisions: list[str] | None = None,
        changes: list[str] | None = None,
        open_questions: list[str] | None = None,
        source_links: list[str] | None = None,
        transcript_excerpt: str | None = None,
    ) -> dict:
        """Store a structured closeout for an owned agent session."""
        async with async_session() as session:
            return await record_session_closeout(
                session,
                agent_kind=agent_kind,
                session_id=session_id,
                cwd=cwd,
                project_ref=project_ref,
                summary=summary,
                decisions=decisions or [],
                changes=changes or [],
                open_questions=open_questions or [],
                source_links=source_links or [],
                transcript_excerpt=transcript_excerpt,
            )

    @mcp.tool()
    async def publish_curated_session_story_tool(
        agent_kind: str,
        session_id: str,
        project_ref: str,
        title: str,
        summary: str,
        direction: str | None = None,
        changes: list[str] | None = None,
        open_loops: list[str] | None = None,
        source_links: list[str] | None = None,
        transcript_excerpt: str | None = None,
        tags: list[str] | None = None,
        actor_name: str | None = None,
    ) -> dict:
        """Publish a curated planning/design story as a progress update."""
        async with async_session() as session:
            return await publish_curated_session_story(
                session,
                agent_kind=agent_kind,
                session_id=session_id,
                project_ref=project_ref,
                title=title,
                summary=summary,
                direction=direction,
                changes=changes or [],
                open_loops=open_loops or [],
                source_links=source_links or [],
                transcript_excerpt=transcript_excerpt,
                tags=tags or [],
                actor_name=actor_name,
            )

    @mcp.tool()
    async def resolve_project_identity(project_hint: str, cwd: str | None = None) -> dict:
        """Resolve a project hint into the canonical brain project identity."""
        async with async_session() as session:
            project = await resolve_project(
                session,
                project_hint=project_hint,
                cwd=cwd,
                source_refs=[project_hint],
                create_if_missing=False,
            )
            if not project:
                return {"error": "Project not found"}
            payload = await build_project_story_payload(session, project.id)
            if not payload:
                return {"error": "Project not found"}
            return {
                "project": payload["project"],
                "aliases": payload.get("aliases", [])[:12],
                "repos": payload.get("repos", [])[:6],
            }

    @mcp.tool()
    async def set_project_focus_state(project_name: str, state: str) -> dict:
        """Set a project's manual focus state: normal, pinned, ignored, or done."""
        if state not in PROJECT_MANUAL_STATES:
            return {"error": f"State must be one of: {', '.join(PROJECT_MANUAL_STATES)}"}
        async with async_session() as session:
            project = await resolve_project(
                session,
                project_hint=project_name,
                source_refs=[project_name],
                create_if_missing=False,
            )
            if not project:
                return {"error": f"Project '{project_name}' not found"}
            snapshot = await store.set_project_manual_state(
                session,
                project_note_id=project.id,
                manual_state=state,
            )
            refreshed = await recompute_project_states(session, project_note_ids=[project.id])
            latest = refreshed[0] if refreshed else snapshot
            return {
                "status": "updated",
                "project": project.title,
                "manual_state": latest.manual_state,
                "active_score": latest.active_score,
                "status_label": latest.status,
            }

    @mcp.tool()
    async def recompute_project_states_tool(project_name: str | None = None) -> dict:
        """Refresh stored project state snapshots."""
        async with async_session() as session:
            project_ids = None
            if project_name:
                project = await resolve_project(
                    session,
                    project_hint=project_name,
                    source_refs=[project_name],
                    create_if_missing=False,
                )
                if not project:
                    return {"error": f"Project '{project_name}' not found"}
                project_ids = [project.id]
            snapshots = await recompute_project_states(session, project_note_ids=project_ids)
            return {
                "status": "completed",
                "projects": [
                    {
                        "project_note_id": str(item.project_note_id),
                        "status": item.status,
                        "active_score": item.active_score,
                    }
                    for item in snapshots
                ],
            }

    @mcp.tool()
    async def create_reminder(text: str, project_name: str | None = None, discord_channel_id: str | None = None) -> dict:
        """Store a reminder and schedule the next Discord notification."""
        async with async_session() as session:
            note = await store.create_note(
                session,
                category="reminder",
                title=text[:120],
                content=text,
                priority="medium",
                discord_channel_id=discord_channel_id,
            )
            project_note_id = None
            if project_name:
                project = await resolve_project(
                    session,
                    project_hint=project_name,
                    source_refs=[project_name],
                    create_if_missing=False,
                )
                if project:
                    project_note_id = project.id
            reminder = await store_reminder(
                session,
                raw_text=text,
                note_id=note.id,
                project_note_id=project_note_id,
                discord_channel_id=discord_channel_id,
            )
        return {
            "status": "stored",
            "reminder_id": str(reminder.id),
            "title": reminder.title,
            **human_datetime_payload(reminder.next_fire_at, prefix="next_fire_at", fallback="unscheduled"),
        }
