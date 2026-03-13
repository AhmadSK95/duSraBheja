"""Source sync services for GitHub and collector imports."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.lib import store
from src.agents.storyteller import extract_story_event
from src.services.identity import ensure_project_aliases, resolve_project
from src.services.indexing import index_artifact
from src.services.project_state import recompute_project_states
from src.services.source_ingest import ingest_source_entries
from src.services.story import publish_story_entry

log = logging.getLogger("brain.services.sync")


def _hash_payload(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


async def import_collector_payload(
    session: AsyncSession,
    *,
    source_type: str,
    source_name: str,
    mode: str,
    device_name: str,
    emit_sync_event: bool = True,
    entries: list[dict],
) -> dict:
    enriched_entries = []
    for entry in entries:
        enriched = dict(entry)
        if not enriched.get("content_hash"):
            enriched["content_hash"] = _hash_payload(
                "|".join(
                    str(part or "")
                    for part in (
                        enriched.get("project_ref"),
                        enriched.get("title"),
                        enriched.get("body_markdown"),
                        enriched.get("summary"),
                    )
                )
            )
        enriched_entries.append(enriched)
    return await ingest_source_entries(
        session,
        source_type=source_type,
        source_name=source_name,
        mode=mode,
        device_name=device_name,
        emit_sync_event=emit_sync_event,
        entries=enriched_entries,
    )


async def run_github_sync(session: AsyncSession) -> dict:
    sync_source = await store.upsert_sync_source(
        session,
        source_type="github",
        name="github-readonly",
        status="active" if settings.github_api_token else "not_configured",
        config={},
    )
    sync_run = await store.start_sync_run(session, sync_source_id=sync_source.id, mode="sync")

    if not settings.github_api_token:
        await store.finish_sync_run(
            session,
            sync_run.id,
            status="skipped",
            items_seen=0,
            items_imported=0,
            error="GITHUB_API_TOKEN is not configured",
        )
        return {"status": "skipped", "reason": "GITHUB_API_TOKEN is not configured"}

    headers = {
        "Authorization": f"Bearer {settings.github_api_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    imported = 0
    items_seen = 0
    project_note_ids_touched: set[str] = set()
    async with httpx.AsyncClient(base_url=settings.github_api_base_url, headers=headers, timeout=30) as client:
        repos_resp = await client.get("/user/repos", params={"per_page": 100, "sort": "updated"})
        repos_resp.raise_for_status()
        repos = repos_resp.json()
        items_seen += len(repos)

        for repo in repos:
            owner = (repo.get("owner") or {}).get("login")
            recent_commits = []
            open_pulls = []
            open_issues = []
            if owner:
                try:
                    commits_resp = await client.get(f"/repos/{owner}/{repo['name']}/commits", params={"per_page": 5})
                    if commits_resp.is_success:
                        recent_commits = commits_resp.json()
                    pulls_resp = await client.get(f"/repos/{owner}/{repo['name']}/pulls", params={"state": "open", "per_page": 5})
                    if pulls_resp.is_success:
                        open_pulls = pulls_resp.json()
                    issues_resp = await client.get(f"/repos/{owner}/{repo['name']}/issues", params={"state": "open", "per_page": 5})
                    if issues_resp.is_success:
                        open_issues = [item for item in issues_resp.json() if "pull_request" not in item]
                except Exception as exc:
                    log.warning("Failed to expand GitHub details for %s: %s", repo["full_name"], exc)

            project_note = await resolve_project(
                session,
                project_hint=repo["name"],
                repo_name=repo["name"],
                source_refs=[repo["full_name"], repo.get("html_url")],
                create_if_missing=True,
            )
            if not project_note:
                continue
            project_note_ids_touched.add(str(project_note.id))
            await ensure_project_aliases(
                session,
                project_note_id=project_note.id,
                title=project_note.title,
                aliases=[repo["name"], repo["full_name"], repo.get("html_url")],
                source_type="github",
                source_ref=repo["full_name"],
            )
            await store.upsert_project_repo(
                session,
                project_note_id=project_note.id,
                repo_name=repo["name"],
                repo_owner=owner,
                repo_url=repo.get("html_url"),
                branch=repo.get("default_branch"),
                local_path=None,
                is_primary=True,
            )

            commit_summary = ", ".join(
                item.get("commit", {}).get("message", "").splitlines()[0]
                for item in recent_commits[:3]
                if item.get("commit", {}).get("message")
            )
            summary = (
                f"Repo {repo['name']} updated at {repo.get('pushed_at') or repo.get('updated_at')}. "
                f"Open PRs: {len(open_pulls)}. Open issues: {len(open_issues)}. "
                f"Recent commits: {commit_summary or 'none'}"
            )
            artifact = await store.create_artifact(
                session,
                content_type="text",
                raw_text=summary,
                summary=repo["full_name"],
                source="github",
                metadata_={
                    "repo": repo["full_name"],
                    "default_branch": repo.get("default_branch"),
                    "recent_commits": recent_commits,
                    "open_pulls": open_pulls,
                    "open_issues": open_issues,
                },
            )
            await store.create_classification(
                session,
                artifact_id=artifact.id,
                category="project",
                confidence=1.0,
                entities=[],
                tags=["github", "repo"],
                priority="medium",
                suggested_action=None,
                model_used="github-sync",
                tokens_used=0,
                cost_usd=0,
                is_final=True,
            )
            source_item, created = await store.upsert_source_item(
                session,
                sync_source_id=sync_source.id,
                external_id=f"repo:{repo['full_name']}",
                title=repo["full_name"],
                summary=summary,
                payload={
                    "repo": repo,
                    "recent_commits": recent_commits,
                    "open_pulls": open_pulls,
                    "open_issues": open_issues,
                },
                content_hash=_hash_payload(repo["full_name"] + str(repo.get("pushed_at"))),
                external_url=repo.get("html_url"),
                project_note_id=project_note.id,
                artifact_id=artifact.id,
                happened_at=datetime.fromisoformat(repo["updated_at"].replace("Z", "+00:00")),
            )
            await publish_story_entry(
                session,
                actor_type="connector",
                actor_name="github",
                entry_type="repo_snapshot",
                title=repo["full_name"],
                body_markdown=summary,
                project_ref=project_note.title,
                tags=["github", "repo"],
                source_links=[repo.get("html_url")] if repo.get("html_url") else [],
                source="github",
                category="project",
                metadata_={"full_name": repo["full_name"], "default_branch": repo.get("default_branch")},
                artifact_id=artifact.id,
                source_item_id=source_item.id,
            )
            if created:
                imported += 1

    await store.finish_sync_run(
        session,
        sync_run.id,
        status="completed",
        items_seen=items_seen,
        items_imported=imported,
    )
    await store.touch_sync_source(session, sync_source.id)
    result = {
        "status": "completed",
        "sync_run_id": str(sync_run.id),
        "sync_source_id": str(sync_source.id),
        "items_seen": items_seen,
        "items_imported": imported,
        "source_name": "github-readonly",
        "source_type": "github",
        "mode": "sync",
    }
    if project_note_ids_touched:
        await recompute_project_states(
            session,
            project_note_ids=[uuid.UUID(value) for value in sorted(project_note_ids_touched)],
        )
    await _publish_sync_event(result)
    return result


async def record_sync_report(
    session: AsyncSession,
    *,
    source_type: str,
    source_name: str,
    mode: str,
    status: str,
    items_seen: int,
    items_imported: int,
    device_name: str | None = None,
    error: str | None = None,
    metadata: dict | None = None,
) -> dict:
    sync_source = await store.upsert_sync_source(
        session,
        source_type=source_type,
        name=source_name,
        status="active" if status not in {"failed", "not_configured"} else status,
        config={"device_name": device_name} if device_name else {},
    )
    sync_run = await store.start_sync_run(
        session,
        sync_source_id=sync_source.id,
        mode=mode,
        metadata_=metadata,
    )
    await store.finish_sync_run(
        session,
        sync_run.id,
        status=status,
        items_seen=items_seen,
        items_imported=items_imported,
        error=error,
    )
    await store.touch_sync_source(session, sync_source.id)
    result = {
        "status": status,
        "sync_source_id": str(sync_source.id),
        "sync_run_id": str(sync_run.id),
        "items_seen": items_seen,
        "items_imported": items_imported,
        "source_name": source_name,
        "source_type": source_type,
        "device_name": device_name,
        "mode": mode,
    }
    if error:
        result["error"] = error
    await _publish_sync_event(result)
    return result


async def _publish_sync_event(payload: dict) -> None:
    from src.worker.main import EVENT_SYNC_COMPLETED, publish_event

    await publish_event(EVENT_SYNC_COMPLETED, payload)


async def _trigger_story_pulse(*, reason: str, metadata: dict) -> None:
    """Retained as a no-op compatibility hook while story-pulse posting is disabled."""
    return None


async def _extract_story_fields(
    session: AsyncSession,
    *,
    source_type: str,
    title: str,
    body_markdown: str,
    project_ref: str | None,
    actor_name: str,
) -> dict:
    if source_type not in {"codex_history", "claude_history"}:
        return {
            "subject_type": "project" if project_ref else "topic",
            "subject_ref": project_ref,
            "entry_type": "context_dump",
            "title": title,
            "summary": body_markdown[:280] if body_markdown else title,
            "decision": None,
            "rationale": None,
            "constraint": None,
            "outcome": None,
            "impact": None,
            "open_question": None,
            "evidence_refs": [],
            "tags": [],
        }

    try:
        return await extract_story_event(
            session,
            title=title,
            body_markdown=body_markdown,
            project_ref=project_ref,
            actor_name=actor_name,
        )
    except Exception:
        return {
            "subject_type": "project" if project_ref else "topic",
            "subject_ref": project_ref,
            "entry_type": "conversation_session",
            "title": title,
            "summary": body_markdown[:280] if body_markdown else title,
            "decision": None,
            "rationale": None,
            "constraint": None,
            "outcome": None,
            "impact": None,
            "open_question": None,
            "evidence_refs": [],
            "tags": ["conversation"],
        }
