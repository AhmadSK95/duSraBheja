"""Source sync services for GitHub and collector imports."""

from __future__ import annotations

import hashlib
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.lib import store
from src.services.story import publish_story_entry


def _hash_payload(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def import_collector_payload(
    session: AsyncSession,
    *,
    source_name: str,
    mode: str,
    device_name: str,
    entries: list[dict],
) -> dict:
    sync_source = await store.upsert_sync_source(
        session,
        source_type="collector",
        name=source_name,
        status="active",
        config={"device_name": device_name},
    )
    sync_run = await store.start_sync_run(session, sync_source_id=sync_source.id, mode=mode)

    imported = 0
    for entry in entries:
        project_ref = entry.get("project_ref")
        title = entry.get("title") or project_ref or "Collector update"
        body_markdown = entry.get("body_markdown") or ""
        payload_hash = _hash_payload(f"{project_ref}|{title}|{body_markdown}")
        happened_at = entry.get("happened_at")
        happened_dt = datetime.fromisoformat(happened_at) if happened_at else None

        project_note = None
        if project_ref:
            project_note = await store.get_or_create_project_note(session, project_ref)
            if entry.get("repo"):
                await store.upsert_project_repo(
                    session,
                    project_note_id=project_note.id,
                    repo_name=entry["repo"].get("name") or project_ref,
                    repo_owner=entry["repo"].get("owner"),
                    repo_url=entry["repo"].get("url"),
                    branch=entry["repo"].get("branch"),
                    local_path=entry["repo"].get("local_path"),
                    is_primary=entry["repo"].get("is_primary", False),
                )

        artifact = await store.create_artifact(
            session,
            content_type="text",
            raw_text=body_markdown,
            summary=title,
            source="collector",
            metadata_={
                "entry_type": entry.get("entry_type", "context_dump"),
                "device_name": device_name,
                "project_ref": project_ref,
                "collector_metadata": entry.get("metadata", {}),
            },
        )
        await store.create_classification(
            session,
            artifact_id=artifact.id,
            category=entry.get("category") or ("project" if project_ref else "note"),
            confidence=1.0,
            entities=[],
            tags=entry.get("tags", []),
            priority="medium",
            suggested_action=None,
            model_used="collector",
            tokens_used=0,
            cost_usd=0,
            is_final=True,
        )
        source_item, created = await store.upsert_source_item(
            session,
            sync_source_id=sync_source.id,
            external_id=entry.get("external_id") or payload_hash,
            title=title,
            summary=entry.get("summary"),
            payload=entry,
            content_hash=payload_hash,
            external_url=entry.get("external_url"),
            project_note_id=project_note.id if project_note else None,
            artifact_id=artifact.id,
            happened_at=happened_dt,
        )
        await publish_story_entry(
            session,
            actor_type="collector",
            actor_name=device_name,
            entry_type=entry.get("entry_type", "context_dump"),
            title=title,
            body_markdown=body_markdown,
            project_ref=project_ref,
            tags=entry.get("tags", []),
            source_links=entry.get("source_links", []),
            source="collector",
            category=entry.get("category", "note"),
            metadata_=entry.get("metadata"),
            happened_at=happened_dt,
            artifact_id=artifact.id,
            source_item_id=source_item.id,
        )
        if created:
            imported += 1

    await store.finish_sync_run(
        session,
        sync_run.id,
        status="completed",
        items_seen=len(entries),
        items_imported=imported,
    )
    await store.touch_sync_source(session, sync_source.id)
    result = {
        "status": "completed",
        "sync_source_id": str(sync_source.id),
        "sync_run_id": str(sync_run.id),
        "items_seen": len(entries),
        "items_imported": imported,
        "source_name": source_name,
        "source_type": "collector",
        "device_name": device_name,
        "mode": mode,
    }
    await _publish_sync_event(result)
    return result


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
    async with httpx.AsyncClient(base_url=settings.github_api_base_url, headers=headers, timeout=30) as client:
        repos_resp = await client.get("/user/repos", params={"per_page": 100, "sort": "updated"})
        repos_resp.raise_for_status()
        repos = repos_resp.json()
        items_seen += len(repos)

        for repo in repos:
            project_note = await store.get_or_create_project_note(session, repo["name"])
            await store.upsert_project_repo(
                session,
                project_note_id=project_note.id,
                repo_name=repo["name"],
                repo_owner=(repo.get("owner") or {}).get("login"),
                repo_url=repo.get("html_url"),
                branch=repo.get("default_branch"),
                local_path=None,
                is_primary=True,
            )

            summary = f"Repo {repo['name']} updated at {repo.get('pushed_at') or repo.get('updated_at')}"
            artifact = await store.create_artifact(
                session,
                content_type="text",
                raw_text=summary,
                summary=repo["full_name"],
                source="github",
                metadata_={"repo": repo["full_name"], "default_branch": repo.get("default_branch")},
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
                payload=repo,
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
