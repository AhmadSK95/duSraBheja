"""MCP tools for project story access and agent publication."""

from mcp.server.fastmcp import FastMCP

from src.database import async_session
from src.lib import store
from src.services.project_state import recompute_project_states
from src.services.query import query_brain
from src.services.reminders import store_reminder
from src.services.story import (
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
            projects = await store.find_notes_by_title(session, project_name, "project")
            if not projects:
                return {"error": f"Project '{project_name}' not found"}

            return await build_project_story_payload(session, projects[0].id)

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
        """Query the brain in answer/latest/timeline/changed_since/sources/project_review mode."""
        async with async_session() as session:
            return await query_brain(session, question=question, mode=mode, use_opus=deep)

    @mcp.tool()
    async def recompute_project_states_tool(project_name: str | None = None) -> dict:
        """Refresh stored project state snapshots."""
        async with async_session() as session:
            project_ids = None
            if project_name:
                matches = await store.find_notes_by_title(session, project_name, "project")
                if not matches:
                    return {"error": f"Project '{project_name}' not found"}
                project_ids = [matches[0].id]
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
                matches = await store.find_notes_by_title(session, project_name, "project")
                if matches:
                    project_note_id = matches[0].id
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
            "next_fire_at": str(reminder.next_fire_at) if reminder.next_fire_at else None,
        }
