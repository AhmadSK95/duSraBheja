"""MCP tools for Brain OS self-description, library access, and secret workflows."""

from __future__ import annotations

import uuid

from mcp.server.fastmcp import FastMCP

from src.constants import BRAIN_CATEGORIES
from src.database import async_session
from src.services.library import build_library_catalog
from src.services.secrets import request_secret_challenge
from src.services.secrets import reveal_secret_once as reveal_secret_once_service


def register(mcp: FastMCP):
    @mcp.tool()
    async def describe_brain_protocol() -> dict:
        """Describe how an external AI agent should connect to and use the brain."""
        return {
            "name": "duSraBheja",
            "purpose": "Ahmad's personal brain — captures Discord inbox, classifies, stores canonically, answers questions via RAG.",
            "tools": [
                "search_brain", "ask_brain", "capture", "get_project_context",
                "get_full_brain_dump", "list_brain_notes", "get_brain_note",
                "update_brain_note", "query_library",
            ],
            "categories": list(BRAIN_CATEGORIES),
            "session_protocol": "Call bootstrap_session at start, publish_session_closeout at end.",
        }

    @mcp.tool()
    async def query_library(
        q: str | None = None,
        record_kind: str | None = None,
        facet: str | None = None,
        limit: int = 25,
    ) -> dict:
        """Query the canonical library directly across threads, episodes, observations, entities, syntheses, and evidence."""
        async with async_session() as session:
            items = await build_library_catalog(
                session,
                q=q,
                record_kind=record_kind,
                facet=facet,
                limit=limit,
            )
        return {
            "count": len(items),
            "items": items,
        }

    @mcp.tool()
    async def request_secret_access(
        purpose: str,
        secret_id: str | None = None,
        alias: str | None = None,
    ) -> dict:
        """Request owner-verified access to a vault secret. The OTP is sent to Ahmad's Discord DM."""
        async with async_session() as session:
            return await request_secret_challenge(
                session,
                requester="mcp",
                purpose=purpose,
                secret_id=uuid.UUID(secret_id) if secret_id else None,
                alias=alias,
            )

    @mcp.tool()
    async def reveal_secret_once(secret_id: str, grant_token: str) -> dict:
        """Reveal a secret once after the Discord DM OTP challenge has been verified."""
        async with async_session() as session:
            return await reveal_secret_once_service(
                session,
                requester="mcp",
                secret_id=uuid.UUID(secret_id),
                grant_token=grant_token,
            )
