"""MCP tool: search_brain — semantic search over stored knowledge."""

from mcp.server.fastmcp import FastMCP

from src.database import async_session
from src.lib.embeddings import embed_text
from src.lib.store import vector_search, get_artifact, get_note


def register(mcp: FastMCP):
    @mcp.tool()
    async def search_brain(
        query: str,
        category: str | None = None,
        limit: int = 10,
        include_content: bool = False,
    ) -> list[dict]:
        """Semantic search over all stored knowledge in the brain.
        Returns matching notes and artifacts ranked by relevance.
        Use this to find specific information stored in the brain.

        Args:
            query: What to search for
            category: Filter to a category (task, project, people, idea, note, resource, reminder, daily_planner, weekly_planner)
            limit: Max results to return (default 10)
            include_content: Return full content or just summaries
        """
        query_embedding = await embed_text(query)

        async with async_session() as session:
            results = await vector_search(
                session, query_embedding, limit=limit, min_similarity=0.3, category=category
            )

            items = []
            for r in results:
                item = {
                    "similarity": round(r["similarity"], 3),
                    "chunk_preview": r["content"][:200] if not include_content else r["content"],
                }

                if r.get("note_id"):
                    note = await get_note(session, r["note_id"])
                    if note:
                        item.update({
                            "id": str(note.id),
                            "type": "note",
                            "title": note.title,
                            "category": note.category,
                            "status": note.status,
                            "created_at": str(note.created_at),
                        })
                        if include_content:
                            item["full_content"] = note.content
                elif r.get("artifact_id"):
                    artifact = await get_artifact(session, r["artifact_id"])
                    if artifact:
                        item.update({
                            "id": str(artifact.id),
                            "type": "artifact",
                            "title": artifact.summary or "Untitled",
                            "category": "artifact",
                            "content_type": artifact.content_type,
                            "created_at": str(artifact.created_at),
                        })
                        if include_content:
                            item["full_content"] = artifact.raw_text

                items.append(item)

            return items
