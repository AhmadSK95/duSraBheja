"""Website management service — CRUD sections, execute changes, deploy."""

from __future__ import annotations

import logging
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.website_builder import (
    generate_code_edits,
    plan_website_change,
    synthesize_project_case_study,
)
from src.models import WebsiteSection

log = logging.getLogger("brain.website")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CSS_PATH = PROJECT_ROOT / "src" / "api" / "static" / "public" / "site.css"
PUBLIC_PY_PATH = PROJECT_ROOT / "src" / "api" / "routes" / "public.py"


# ── CRUD ────────────────────────────────────────────────────────


async def list_page_sections(session: AsyncSession, page: str) -> list[WebsiteSection]:
    """List all visible sections for a page, ordered by sort_order."""
    result = await session.execute(
        select(WebsiteSection)
        .where(WebsiteSection.page == page, WebsiteSection.visible.is_(True))
        .order_by(WebsiteSection.sort_order)
    )
    return list(result.scalars().all())


async def list_all_sections(session: AsyncSession) -> list[WebsiteSection]:
    """List all sections across all pages."""
    result = await session.execute(
        select(WebsiteSection).order_by(WebsiteSection.page, WebsiteSection.sort_order)
    )
    return list(result.scalars().all())


async def get_section(session: AsyncSession, page: str, section_key: str) -> WebsiteSection | None:
    result = await session.execute(
        select(WebsiteSection).where(
            WebsiteSection.page == page,
            WebsiteSection.section_key == section_key,
        )
    )
    return result.scalar_one_or_none()


async def upsert_section(
    session: AsyncSession,
    *,
    page: str,
    section_key: str,
    section_type: str,
    sort_order: int = 0,
    title: str | None = None,
    content: dict | None = None,
    style_hints: dict | None = None,
    visible: bool = True,
    created_by: str = "brain",
    metadata: dict | None = None,
) -> WebsiteSection:
    """Create or update a website section."""
    now = datetime.now(timezone.utc)
    existing = await get_section(session, page, section_key)
    if existing:
        existing.section_type = section_type
        existing.sort_order = sort_order
        existing.title = title
        existing.content = content or {}
        existing.style_hints = style_hints or {}
        existing.visible = visible
        existing.updated_at = now
        if metadata:
            existing.metadata_ = metadata
        await session.flush()
        return existing

    section = WebsiteSection(
        page=page,
        section_key=section_key,
        section_type=section_type,
        sort_order=sort_order,
        title=title,
        content=content or {},
        style_hints=style_hints or {},
        visible=visible,
        created_by=created_by,
        created_at=now,
        updated_at=now,
        metadata_=metadata or {},
    )
    session.add(section)
    await session.flush()
    return section


async def delete_section(session: AsyncSession, page: str, section_key: str) -> bool:
    result = await session.execute(
        delete(WebsiteSection).where(
            WebsiteSection.page == page,
            WebsiteSection.section_key == section_key,
        )
    )
    return result.rowcount > 0


async def reorder_sections(session: AsyncSession, page: str, ordered_keys: list[str]) -> None:
    """Reorder sections within a page by the given key order."""
    for i, key in enumerate(ordered_keys):
        await session.execute(
            update(WebsiteSection)
            .where(WebsiteSection.page == page, WebsiteSection.section_key == key)
            .values(sort_order=i, updated_at=datetime.now(timezone.utc))
        )


def _sections_to_dicts(sections: list[WebsiteSection]) -> list[dict]:
    """Convert section models to dicts for the agent prompt."""
    return [
        {
            "page": s.page,
            "section_key": s.section_key,
            "section_type": s.section_type,
            "sort_order": s.sort_order,
            "title": s.title,
            "content": s.content,
            "style_hints": s.style_hints,
            "visible": s.visible,
        }
        for s in sections
    ]


# ── Execute website change ──────────────────────────────────────


async def execute_website_change(
    session: AsyncSession,
    instruction: str,
    *,
    taste_profile: str | None = None,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """End-to-end: understand request -> plan changes -> execute -> deploy if needed."""
    trace_id = trace_id or uuid.uuid4()

    # 1. Gather current state
    current_sections = await list_all_sections(session)
    sections_data = _sections_to_dicts(current_sections)

    # 2. Read current code files
    current_css = CSS_PATH.read_text() if CSS_PATH.exists() else ""

    # 3. Plan via agent
    plan = await plan_website_change(
        session,
        instruction=instruction,
        current_sections=sections_data,
        taste_profile=taste_profile,
        current_css=current_css,
        trace_id=trace_id,
    )

    # 4. Execute content changes (DB)
    content_changes = plan.get("content_changes") or []
    for change in content_changes:
        action = change.get("action", "create")
        if action in ("create", "update"):
            await upsert_section(
                session,
                page=change.get("page", "home"),
                section_key=change.get("section_key", "untitled"),
                section_type=change.get("section_type", "text_block"),
                sort_order=change.get("sort_order", 0),
                title=change.get("title"),
                content=change.get("content") or {},
                style_hints=change.get("style_hints") or {},
                created_by="brain",
            )
        elif action == "delete":
            await delete_section(
                session,
                change.get("page", "home"),
                change.get("section_key", ""),
            )

    await session.commit()

    # 5. Execute code changes (file edits) if needed
    code_changes = plan.get("code_changes") or []
    files_modified = []
    if code_changes:
        file_contents = {}
        for change in code_changes:
            fpath = PROJECT_ROOT / change.get("file", "")
            if fpath.exists():
                file_contents[change["file"]] = fpath.read_text()

        if file_contents:
            edits = await generate_code_edits(
                session,
                plan=plan,
                file_contents=file_contents,
                trace_id=trace_id,
            )
            for edit in edits:
                fpath = PROJECT_ROOT / edit.get("file", "")
                if fpath.exists():
                    old = edit.get("old_string", "")
                    new = edit.get("new_string", "")
                    if old and old != new:
                        text = fpath.read_text()
                        if old in text:
                            fpath.write_text(text.replace(old, new, 1))
                            files_modified.append(str(edit["file"]))

    tier = plan.get("tier", "content")
    summary = plan.get("explanation", "Website updated.")

    # Phase 11: Auto-commit code changes + ingest record into brain
    commit_hash = ""
    if files_modified:
        commit_hash = await _auto_commit_and_track(
            session, summary, files_modified
        )

    return {
        "ok": True,
        "summary": summary,
        "tier": tier,
        "content_changes": len(content_changes),
        "code_changes": len(files_modified),
        "files_modified": files_modified,
        "commit": commit_hash,
    }


# ── Taste refresh ───────────────────────────────────────────────


async def refresh_website_taste(
    session: AsyncSession,
    *,
    taste_profile: str | None = None,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Periodic: compare brain's current focus with site state and adjust."""
    return await execute_website_change(
        session,
        instruction=(
            "Review the current site sections and reorder/update them to reflect "
            "my current focus, active projects, and interests. Don't change everything "
            "— just adjust emphasis where it's drifted."
        ),
        taste_profile=taste_profile,
        trace_id=trace_id,
    )


# ── Case study generation ───────────────────────────────────────


async def generate_case_study(
    session: AsyncSession,
    *,
    project_name: str,
    evidence_text: str,
    use_opus: bool = False,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Generate a case study and return the structured JSON."""
    return await synthesize_project_case_study(
        session,
        project_name=project_name,
        evidence_text=evidence_text,
        use_opus=use_opus,
        trace_id=trace_id,
    )


# ── Git state awareness ────────────────────────────────────────


def _run_git(cmd: list[str]) -> str:
    """Run a git command in the project root, return stdout."""
    try:
        return subprocess.check_output(
            cmd, cwd=str(PROJECT_ROOT), text=True, timeout=30
        ).strip()
    except Exception:
        return ""


def get_site_git_state() -> dict:
    """Brain knows its own codebase state."""
    return {
        "current_branch": _run_git(["git", "branch", "--show-current"]),
        "last_commit": _run_git(["git", "log", "-1", "--oneline"]),
        "uncommitted_changes": _run_git(["git", "status", "--short"]),
    }


async def _auto_commit_and_track(
    session: AsyncSession,
    change_summary: str,
    files_modified: list[str],
) -> str:
    """Auto-commit code changes, push, and ingest a record into the brain."""
    # Commit
    for f in files_modified:
        _run_git(["git", "add", f])
    msg = f"brain: {change_summary[:72]}"
    _run_git(["git", "commit", "-m", msg])
    commit_hash = _run_git(["git", "log", "-1", "--format=%h"])

    # Push (non-blocking, best-effort)
    _run_git(["git", "push", "origin", "main"])

    # Ingest a record so the brain remembers what it changed
    try:
        from src.services.story import publish_story_entry

        files_str = ", ".join(files_modified[:5])
        await publish_story_entry(
            session,
            actor_type="system",
            actor_name="website-builder",
            subject_type="project",
            subject_ref="duSraBheja",
            entry_type="site_update",
            title=f"Site update: {change_summary[:80]}",
            body_markdown=(
                f"Updated website: {change_summary}\n\n"
                f"Files: {files_str}\n"
                f"Commit: {commit_hash}"
            ),
            summary=change_summary[:240],
            source="agent",
            category="project",
            tags=["website", "auto-deploy", "site-update"],
        )
    except Exception:
        log.exception("Failed to ingest site update record")

    return commit_hash
