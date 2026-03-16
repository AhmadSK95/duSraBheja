"""Brain OS self-description and capability catalog."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.lib import store
from src.services.providers import provider_registry_summary


def _capability_seed() -> list[dict[str, Any]]:
    base_url = (settings.app_base_url or "http://127.0.0.1:8000").rstrip("/")
    return [
        {
            "capability_key": "protocol:http",
            "title": "Private HTTP API",
            "summary": "Bearer-token API for ingest, query, bootstrap, closeout, library inspection, and secret workflows.",
            "protocol": "http",
            "payload": {
                "base_url": base_url,
                "important_routes": [
                    "/api/query",
                    "/api/agent/session/bootstrap",
                    "/api/agent/session/closeout",
                    "/api/agent/session/story",
                    "/api/brain/self",
                    "/api/library",
                    "/api/secrets/challenge",
                ],
            },
        },
        {
            "capability_key": "protocol:mcp",
            "title": "MCP Brain Tools",
            "summary": "Tool-facing protocol for Claude Code, Codex, and other MCP-capable agents.",
            "protocol": "mcp",
            "payload": {
                "transport": settings.mcp_transport,
                "port": settings.mcp_port,
                "tool_names": [
                    "bootstrap_session",
                    "query_brain_mode",
                    "publish_progress",
                    "publish_session_closeout",
                    "publish_curated_session_story_tool",
                    "describe_brain_protocol",
                    "query_library",
                    "request_secret_access",
                    "reveal_secret_once",
                ],
            },
        },
        {
            "capability_key": "workflow:agent-loop",
            "title": "Owned Agent Loop",
            "summary": "Every Codex or Claude session should bootstrap from the brain, optionally publish progress, then close out back into it.",
            "protocol": "workflow",
            "payload": {
                "bootstrap": {
                    "http": "/api/agent/session/bootstrap",
                    "mcp_tool": "bootstrap_session",
                },
                "publish_progress": {
                    "http": "/api/agent/session/story",
                    "mcp_tool": "publish_curated_session_story_tool",
                },
                "closeout": {
                    "http": "/api/agent/session/closeout",
                    "mcp_tool": "publish_session_closeout",
                },
            },
        },
        {
            "capability_key": "security:secret-vault",
            "title": "Owner-Verified Secret Vault",
            "summary": "High-sensitivity values are stored encrypted, versioned, masked everywhere else, and revealed either directly in the trusted owner DM lane or via dashboard/API auth plus a fresh Discord DM OTP.",
            "protocol": "security",
            "payload": {
                "challenge_ttl_minutes": settings.secret_challenge_ttl_minutes,
                "grant_ttl_seconds": settings.secret_access_grant_ttl_seconds,
                "max_attempts": settings.secret_challenge_max_attempts,
                "delivery": "discord_dm",
                "owner_dm_trusted_lane": True,
                "routes": [
                    "/api/secrets",
                    "/api/secrets/challenge",
                    "/api/secrets/verify",
                    "/api/secrets/{secret_id}/reveal",
                ],
            },
        },
        {
            "capability_key": "surface:public-site",
            "title": "Public Brain Surface",
            "summary": "Public portfolio, project case-study pages, and a recruiter/collaborator chatbot that only read from approved public facts.",
            "protocol": "http",
            "payload": {
                "base_url": (settings.public_base_url or settings.app_base_url).rstrip("/"),
                "routes": ["/", "/about", "/projects", "/open-brain", "/api/public/profile", "/api/public/chat"],
            },
        },
        {
            "capability_key": "config:providers",
            "title": "Provider Registry",
            "summary": "Role-based provider bindings for classifier, reasoning, merge, embed, transcribe, public chat, and web research.",
            "protocol": "config",
            "payload": provider_registry_summary(),
        },
    ]


async def ensure_capability_records(session: AsyncSession) -> list:
    records = []
    for seed in _capability_seed():
        records.append(
            await store.upsert_capability_record(
                session,
                capability_key=seed["capability_key"],
                title=seed["title"],
                summary=seed["summary"],
                protocol=seed["protocol"],
                visibility="private",
                payload=seed["payload"],
                metadata_={"managed_by": "brain_os"},
            )
        )
    return records


async def build_brain_self_description(session: AsyncSession) -> dict[str, Any]:
    capability_records = await ensure_capability_records(session)
    capabilities = [
        {
            "key": record.capability_key,
            "title": record.title,
            "summary": record.summary,
            "protocol": record.protocol,
            "visibility": record.visibility,
            "payload": record.payload,
        }
        for record in capability_records
    ]
    return {
        "name": "duSraBheja Brain OS",
        "display_timezone": settings.digest_timezone,
        "protocols": {
            "http": {
                "base_url": (settings.app_base_url or "http://127.0.0.1:8000").rstrip("/"),
                "auth": "Bearer API token or authenticated dashboard session for dashboard-coupled routes.",
            },
            "public_http": {
                "base_url": (settings.public_base_url or settings.app_base_url or "http://127.0.0.1:8000").rstrip("/"),
                "auth": "Public routes are anonymous but sandboxed to approved public facts only. Public chat also requires Turnstile and rate limiting.",
            },
            "mcp": {
                "transport": settings.mcp_transport,
                "port": settings.mcp_port,
                "auth": "Private deployment; pair Claude Code or Codex to the MCP server and use the registered tools.",
            },
            "cli": {
                "bootstrap": "./.venv/bin/python scripts/brain_session.py bootstrap --agent-kind <codex|claude> --project-hint <project>",
                "closeout": "./.venv/bin/python scripts/brain_session.py closeout --agent-kind <codex|claude> --session-id <id> --project-ref <project> --summary \"...\"",
            },
        },
        "flows": {
            "bootstrap": "Start by calling bootstrap_session or POST /api/agent/session/bootstrap with the current project hint and cwd.",
            "publish_progress": "During substantive work, publish curated progress updates so project retrieval stays fresh.",
            "closeout": "At the end of the session, publish a structured closeout with summary, changes, decisions, and open questions.",
            "secret_access": "Owner DM is the trusted reveal lane. Dashboard and API reveals require a challenge, a Discord DM OTP, then a short-lived reveal grant.",
        },
        "capabilities": capabilities,
        "mcp_quickstart": [
            "Register the MCP server exposed by duSraBheja.",
            "Call describe_brain_protocol first if you need the latest contract.",
            "Call bootstrap_session before doing repo work.",
            "Use query_library or query_brain_mode while working.",
            "Publish progress and close out when finished.",
        ],
        "what_to_avoid": [
            "Do not send secrets to general ask-brain or server channels.",
            "Do not publish raw planning chatter as durable memory; promote decisions, blockers, rationale, and concrete changes instead.",
            "Do not treat story output as canonical truth; the canonical library is observations, episodes, threads, entities, evidence, and syntheses.",
            "Do not let public routes or public chat read directly from private memory; use approved public facts and snapshots only.",
        ],
    }
