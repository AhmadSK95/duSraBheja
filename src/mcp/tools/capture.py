"""MCP tool: capture_thought — save a thought from a coding session."""

from mcp.server.fastmcp import FastMCP

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
            category: Optional pre-classification (task, project, people, idea, note, resource, reminder, daily_planner, weekly_planner)
            tags: Optional tags
            priority: Priority level (low, medium, high, urgent)
        """
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
            "status": "queued",
            "message": f"Captured: {text[:100]}",
        }
