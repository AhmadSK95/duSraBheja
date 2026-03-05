"""MCP tools: get_project_context, get_full_brain_dump, list_notes, get_note, update_note."""

from mcp.server.fastmcp import FastMCP

from src.database import async_session
from src.lib import store


def register(mcp: FastMCP):
    @mcp.tool()
    async def get_project_context(project_name: str) -> dict:
        """Get full context for a specific project: description, tasks, recent ideas,
        related people, and recent activity. Use this at the start of a coding session
        to understand project state.

        Args:
            project_name: Name of the project to get context for
        """
        async with async_session() as session:
            # Find the project note
            projects = await store.find_notes_by_title(session, project_name, "project")
            if not projects:
                return {"error": f"Project '{project_name}' not found"}

            project = projects[0]

            # Get related tasks
            tasks = await store.list_notes(session, category="task", limit=20)
            # Filter tasks that mention the project name
            related_tasks = [
                {"id": str(t.id), "title": t.title, "status": t.status, "priority": t.priority}
                for t in tasks
                if project_name.lower() in (t.title + (t.content or "")).lower()
            ]

            # Get related ideas
            ideas = await store.list_notes(session, category="idea", limit=20)
            related_ideas = [
                {"id": str(i.id), "title": i.title}
                for i in ideas
                if project_name.lower() in (i.title + (i.content or "")).lower()
            ]

            # Get related people
            people = await store.list_notes(session, category="people", limit=20)
            related_people = [
                {"id": str(p.id), "title": p.title}
                for p in people
                if project_name.lower() in (p.content or "").lower()
            ]

            return {
                "project": {
                    "id": str(project.id),
                    "title": project.title,
                    "content": project.content,
                    "status": project.status,
                    "tags": list(project.tags or []),
                    "created_at": str(project.created_at),
                    "updated_at": str(project.updated_at),
                },
                "tasks": related_tasks[:10],
                "ideas": related_ideas[:5],
                "people": related_people[:5],
            }

    @mcp.tool()
    async def get_full_brain_dump(
        categories: list[str] | None = None,
        since_days: int = 30,
    ) -> dict:
        """Get a comprehensive dump of brain contents for establishing context.
        Returns summaries of all active notes grouped by category.
        Use this to give an AI agent full awareness of the user's world.

        Args:
            categories: Filter to specific categories (default: all)
            since_days: How far back to look (default: 30 days)
        """
        all_categories = categories or ["task", "project", "people", "idea", "note", "reminder", "planner"]
        result = {}

        async with async_session() as session:
            for cat in all_categories:
                notes = await store.list_notes(session, category=cat, limit=50)
                result[cat] = [
                    {
                        "id": str(n.id),
                        "title": n.title,
                        "summary": (n.content or "")[:200],
                        "status": n.status,
                        "priority": n.priority,
                        "tags": list(n.tags or []),
                        "updated_at": str(n.updated_at),
                    }
                    for n in notes
                ]

        return result

    @mcp.tool()
    async def list_brain_notes(
        category: str,
        status: str = "active",
        limit: int = 25,
    ) -> list[dict]:
        """List notes in a specific category. Returns title, status, priority, and creation date.

        Args:
            category: One of: task, project, people, idea, note, reminder, planner
            status: Filter by status (active, completed, archived)
            limit: Max notes to return
        """
        async with async_session() as session:
            notes = await store.list_notes(session, category=category, status=status, limit=limit)
            return [
                {
                    "id": str(n.id),
                    "title": n.title,
                    "status": n.status,
                    "priority": n.priority,
                    "tags": list(n.tags or []),
                    "created_at": str(n.created_at),
                    "updated_at": str(n.updated_at),
                }
                for n in notes
            ]

    @mcp.tool()
    async def get_brain_note(note_id: str) -> dict:
        """Get the full content of a specific note by ID.

        Args:
            note_id: UUID of the note
        """
        import uuid

        async with async_session() as session:
            note = await store.get_note(session, uuid.UUID(note_id))
            if not note:
                return {"error": "Note not found"}

            related = await store.get_related(session, "note", note.id)

            return {
                "id": str(note.id),
                "category": note.category,
                "title": note.title,
                "content": note.content,
                "status": note.status,
                "priority": note.priority,
                "tags": list(note.tags or []),
                "due_date": str(note.due_date) if note.due_date else None,
                "remind_at": str(note.remind_at) if note.remind_at else None,
                "metadata": note.metadata_,
                "created_at": str(note.created_at),
                "updated_at": str(note.updated_at),
                "related": [
                    {"type": l.target_type, "id": str(l.target_id), "relation": l.relation}
                    for l in related
                ],
            }

    @mcp.tool()
    async def update_brain_note(
        note_id: str,
        content: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Update an existing note. Only provided fields are changed.
        Use this to mark tasks complete, update project status, etc.

        Args:
            note_id: UUID of the note to update
            content: New content (markdown)
            status: New status (active, completed, archived, snoozed)
            priority: New priority (low, medium, high, urgent)
            tags: New tags list
        """
        import uuid

        kwargs = {}
        if content is not None:
            kwargs["content"] = content
        if status is not None:
            kwargs["status"] = status
        if priority is not None:
            kwargs["priority"] = priority
        if tags is not None:
            kwargs["tags"] = tags

        if not kwargs:
            return {"error": "No fields to update"}

        async with async_session() as session:
            note = await store.update_note(session, uuid.UUID(note_id), **kwargs)
            if not note:
                return {"error": "Note not found"}

            return {
                "id": str(note.id),
                "updated_fields": list(kwargs.keys()),
                "title": note.title,
                "status": note.status,
            }
