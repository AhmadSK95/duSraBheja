"""Shared enums and helpers for brain categories and sources."""

from __future__ import annotations

from typing import Iterable

BRAIN_CATEGORIES = (
    "task",
    "project",
    "people",
    "idea",
    "note",
    "resource",
    "reminder",
    "daily_planner",
    "weekly_planner",
)

LEGACY_CATEGORY_ALIASES = {
    "planner": "daily_planner",
}

CATEGORY_CHANNELS = {
    "task": "tasks",
    "project": "projects",
    "people": "people",
    "idea": "ideas",
    "note": "notes",
    "resource": "resources",
    "reminder": "reminders",
    "daily_planner": "daily-planner",
    "weekly_planner": "weekly-planner",
}

MERGEABLE_CATEGORIES = {"project", "people", "resource"}

SOURCE_TYPES = (
    "discord",
    "collector",
    "codex_history",
    "claude_history",
    "github",
    "gmail",
    "drive",
    "google_keep",
    "apple_notes",
    "knowledge",
    "agent",
    "manual",
    "mcp",
)

QUERY_MODES = (
    "answer",
    "latest",
    "timeline",
    "changed_since",
    "sources",
    "project_review",
)

PROJECT_STATUSES = (
    "active",
    "warming_up",
    "blocked",
    "dormant",
    "done",
    "uncertain",
)

PROJECT_MANUAL_STATES = (
    "normal",
    "pinned",
    "ignored",
    "done",
)


def normalize_category(category: str | None, default: str = "note") -> str:
    """Normalize legacy categories into the canonical taxonomy."""
    if not category:
        return default

    normalized = category.strip().lower().replace(" ", "_")
    normalized = LEGACY_CATEGORY_ALIASES.get(normalized, normalized)
    if normalized not in BRAIN_CATEGORIES:
        return default
    return normalized


def is_valid_category(category: str | None) -> bool:
    return normalize_category(category) == (category or "").strip().lower().replace(" ", "_")


def normalize_tags(tags: Iterable[str] | None) -> list[str]:
    if not tags:
        return []
    result = []
    seen = set()
    for tag in tags:
        cleaned = tag.strip().lower().replace(" ", "-")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result
