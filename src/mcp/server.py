"""FastMCP server — exposes brain tools to Claude Code / Codex."""

import logging

from mcp.server.fastmcp import FastMCP

from src.config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("brain-mcp")

mcp = FastMCP("duSraBheja Brain", host="0.0.0.0", port=settings.mcp_port)


# ── Import tools (registers them with the mcp instance) ─────────
from src.mcp.tools.search import register as register_search
from src.mcp.tools.ask import register as register_ask
from src.mcp.tools.capture import register as register_capture
from src.mcp.tools.context import register as register_context
from src.mcp.tools.protocol import register as register_protocol
from src.mcp.tools.story import register as register_story
from src.mcp.tools.website import register as register_website

register_search(mcp)
register_ask(mcp)
register_capture(mcp)
register_context(mcp)
register_protocol(mcp)
register_story(mcp)
register_website(mcp)


def main():
    """Run MCP server."""
    if settings.mcp_transport == "streamable-http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
