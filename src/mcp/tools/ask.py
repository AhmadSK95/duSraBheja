"""MCP tool: ask_brain — full RAG question answering with citations."""

from mcp.server.fastmcp import FastMCP

from src.database import async_session
from src.agents.retriever import answer_question


def register(mcp: FastMCP):
    @mcp.tool()
    async def ask_brain(
        question: str,
        category: str | None = None,
    ) -> dict:
        """Ask a natural language question to the brain. Returns an AI-synthesized answer
        with citations to specific artifacts and notes.
        Use this for complex queries that need reasoning over multiple sources.

        Args:
            question: What you want to know
            category: Optional category filter (task, project, people, idea, note, resource, reminder, daily_planner, weekly_planner)
        """
        async with async_session() as session:
            result = await answer_question(
                session,
                question=question,
                category=category,
            )

        return {
            "answer": result["answer"],
            "sources": result["sources"],
            "confidence": result["confidence"],
        }
