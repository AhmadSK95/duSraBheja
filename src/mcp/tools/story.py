"""MCP tools for project story access and agent publication."""

from mcp.server.fastmcp import FastMCP

from src.database import async_session
from src.lib import store
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
