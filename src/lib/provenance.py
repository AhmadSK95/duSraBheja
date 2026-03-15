"""Helpers for classifying direct vs derived signals."""

from __future__ import annotations

DERIVED_ENTRY_TYPES = {
    "synapse",
    "blind_spot",
    "research_thread",
    "knowledge_refresh",
    "voice_refresh",
}

DIRECT_AGENT_ENTRY_TYPES = {
    "session_closeout",
    "progress_update",
    "conversation_session",
    "decision",
}

DIRECT_SYNC_SOURCES = {
    "apple_notes",
    "browser_activity",
    "claude_history",
    "codex_history",
    "collector",
    "discord",
    "github",
    "gmail",
    "google_keep",
    "drive",
    "life_export",
}


def signal_kind_for_artifact(*, source: str | None, capture_context: str | None = None) -> str:
    lowered_source = (source or "").strip().lower()
    lowered_context = (capture_context or "").strip().lower()
    if lowered_source in {"ask-brain", "agent", "codex", "claude"}:
        return "direct_agent"
    if lowered_context in {"feedback", "inbox", "startup_replay"}:
        return "direct_human"
    if lowered_source in DIRECT_SYNC_SOURCES:
        return "direct_sync"
    if lowered_source in {"manual", "discord"}:
        return "direct_human"
    return "direct_sync"


def signal_kind_for_event(*, entry_type: str | None, actor_type: str | None) -> str:
    lowered_entry_type = (entry_type or "").strip().lower()
    lowered_actor = (actor_type or "").strip().lower()
    if lowered_entry_type in DERIVED_ENTRY_TYPES or lowered_actor in {"connector", "system"}:
        return "derived_system"
    if lowered_entry_type in DIRECT_AGENT_ENTRY_TYPES or lowered_actor == "agent":
        return "direct_agent"
    if lowered_actor == "human":
        return "direct_human"
    return "direct_sync"
