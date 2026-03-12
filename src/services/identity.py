"""Canonical project identity helpers."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import store


def normalize_alias(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
    return cleaned.strip("-")


def alias_candidates(*values: str | None) -> list[str]:
    seen: set[str] = set()
    aliases: list[str] = []
    for value in values:
        raw = (value or "").strip()
        if not raw:
            continue
        candidates = {raw}
        leaf = Path(raw).name.strip()
        if leaf:
            candidates.add(leaf)
        if "/" in raw:
            candidates.add(raw.rstrip("/").split("/")[-1])
        if ":" in raw:
            candidates.add(raw.split(":")[-1].strip())
        for candidate in candidates:
            normalized = normalize_alias(candidate)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            aliases.append(candidate.strip())
    return aliases


async def ensure_project_aliases(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID,
    title: str,
    aliases: list[str],
    source_type: str | None = None,
    source_ref: str | None = None,
    is_manual: bool = False,
) -> None:
    for alias in alias_candidates(title, *aliases):
        await store.upsert_project_alias(
            session,
            project_note_id=project_note_id,
            alias=alias,
            source_type=source_type,
            source_ref=source_ref,
            confidence=1.0 if is_manual else 0.82,
            is_manual=is_manual,
        )


async def resolve_project(
    session: AsyncSession,
    *,
    project_hint: str | None = None,
    cwd: str | None = None,
    repo_name: str | None = None,
    source_refs: list[str] | None = None,
    create_if_missing: bool = False,
) -> object | None:
    for candidate in alias_candidates(project_hint, cwd, repo_name, *(source_refs or [])):
        project_alias = await store.resolve_project_alias(session, candidate)
        if project_alias:
            return await store.get_note(session, project_alias.project_note_id)

        matches = await store.find_notes_by_title(session, candidate, "project")
        if matches:
            project = matches[0]
            await ensure_project_aliases(
                session,
                project_note_id=project.id,
                title=project.title,
                aliases=[candidate],
                source_type="resolver",
                source_ref=candidate,
            )
            return project

    if not create_if_missing:
        return None

    fallback_title = next((item for item in alias_candidates(project_hint, cwd, repo_name) if item), None)
    if not fallback_title:
        return None

    project = await store.get_or_create_project_note(session, fallback_title)
    await ensure_project_aliases(
        session,
        project_note_id=project.id,
        title=project.title,
        aliases=alias_candidates(project_hint, cwd, repo_name, *(source_refs or [])),
        source_type="resolver",
        source_ref=fallback_title,
    )
    return project


async def infer_project_from_text(session: AsyncSession, text: str) -> object | None:
    lowered = (text or "").lower()
    for alias in await store.list_active_project_aliases(session, limit=50):
        if alias.lower() in lowered:
            return await resolve_project(session, project_hint=alias)
    return None
