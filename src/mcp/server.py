"""FastMCP server — exposes brain tools to Claude Code / Codex."""

import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from src.config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("brain-mcp")

mcp = FastMCP("duSraBheja Brain")


# ── Import tools (registers them with the mcp instance) ─────────
from src.mcp.tools.search import register as register_search
from src.mcp.tools.ask import register as register_ask
from src.mcp.tools.capture import register as register_capture
from src.mcp.tools.context import register as register_context

register_search(mcp)
register_ask(mcp)
register_capture(mcp)
register_context(mcp)


def main():
    """Run MCP server."""
    if settings.mcp_transport == "streamable-http":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=settings.mcp_port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
