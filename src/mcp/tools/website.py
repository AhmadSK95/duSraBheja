"""MCP tools: manage_website, get_site_status."""

from mcp.server.fastmcp import FastMCP

from src.database import async_session
from src.services.website import execute_website_change, get_site_git_state, list_all_sections


def register(mcp: FastMCP):
    @mcp.tool()
    async def manage_website(instruction: str) -> str:
        """Modify the public website. The brain understands the instruction,
        decides what to change, and executes it.

        Examples:
        - "add a section about music I love"
        - "make the projects page more technical"
        - "change the accent color to more purple"
        - "remove the photo from the contact page"
        - "reorder home page to put projects first"

        Args:
            instruction: Natural language description of the website change
        """
        async with async_session() as session:
            result = await execute_website_change(session, instruction)
        return result["summary"]

    @mcp.tool()
    async def get_site_status() -> dict:
        """Get the current website status: git state, deployed sections, and section counts.

        Use this to check what's currently live on the site before making changes.
        """
        git_state = get_site_git_state()
        async with async_session() as session:
            sections = await list_all_sections(session)

        page_counts: dict[str, int] = {}
        for s in sections:
            page_counts[s.page] = page_counts.get(s.page, 0) + 1

        return {
            "git": git_state,
            "total_sections": len(sections),
            "page_counts": page_counts,
            "sections": [
                {
                    "page": s.page,
                    "key": s.section_key,
                    "type": s.section_type,
                    "title": s.title,
                    "visible": s.visible,
                }
                for s in sections
            ],
        }
