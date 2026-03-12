"""MCP tool: ask_brain — full RAG question answering with citations."""

from mcp.server.fastmcp import FastMCP

from src.database import async_session
from src.services.query import query_brain


def register(mcp: FastMCP):
    @mcp.tool()
    async def ask_brain(
        question: str,
        category: str | None = None,
        mode: str | None = None,
    ) -> dict:
        """Ask a natural language question to the brain. Returns an AI-synthesized answer
        with citations to specific artifacts and notes.
        Use this for complex queries that need reasoning over multiple sources.

        Args:
            question: What you want to know
            category: Optional category filter (task, project, people, idea, note, resource, reminder, daily_planner, weekly_planner)
            mode: Optional query mode (answer, latest, timeline, changed_since, sources)
        """
        async with async_session() as session:
            result = await query_brain(
                session,
                question=question,
                mode=mode,
                category=category,
            )

        return {
            "answer": result["answer"],
            "sources": result["sources"],
            "brain_sources": result.get("brain_sources", []),
            "web_sources": result.get("web_sources", []),
            "confidence": result["confidence"],
            "mode": result.get("mode"),
        }
