"""MCP tool: capture_thought — save a thought from a coding session."""

import uuid

from mcp.server.fastmcp import FastMCP

from src.database import async_session
from src.lib.store import create_artifact


def register(mcp: FastMCP):
    @mcp.tool()
    async def capture_thought(
        text: str,
        category: str | None = None,
        tags: list[str] | None = None,
        priority: str = "medium",
    ) -> dict:
        """Capture a new thought, idea, task, or note into the brain.
        If category is not specified, the classifier agent will determine it.
        Use this when you want to save information from a coding session.

        Args:
            text: The thought to capture
            category: Optional pre-classification (task, project, people, idea, note, reminder, planner)
            tags: Optional tags
            priority: Priority level (low, medium, high, urgent)
        """
        async with async_session() as session:
            artifact = await create_artifact(
                session,
                content_type="text",
                raw_text=text,
                source="mcp",
                metadata_={"tags": tags or [], "priority": priority},
            )

        # Enqueue classification
        from src.worker.main import enqueue_ingest

        await enqueue_ingest(
            discord_message_id=None,
            discord_channel_id="mcp",
            text=text,
            attachments=[],
            force_category=category,
            source="mcp",
        )

        return {
            "artifact_id": str(artifact.id),
            "status": "processing",
            "message": f"Captured: {text[:100]}",
        }
