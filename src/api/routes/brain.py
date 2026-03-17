"""Primary API routes for the private brain service."""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.schemas import (
    AgentSessionStoryRequest,
    AgentSessionBootstrapRequest,
    AgentSessionCloseoutRequest,
    CollectorIngestRequest,
    ManualIngestRequest,
    ProjectManualStateRequest,
    ProjectStateRefreshRequest,
    QueryRequest,
    SecretChallengeRequest,
    SecretRevealRequest,
    SecretVerifyRequest,
    ReminderCreateRequest,
    SyncReportRequest,
    SyncRunResponse,
)
from src.constants import PROJECT_MANUAL_STATES
from src.database import async_session
from src.lib.auth import require_api_token, require_dashboard_token
from src.lib.embeddings import embed_text
from src.lib.time import human_datetime_payload
from src.lib.store import get_note, vector_search
from src.lib import store
from src.services.brain_os import build_brain_self_description
from src.services.digest import generate_or_refresh_digest
from src.services.identity import resolve_project
from src.services.library import build_final_stored_data, build_library_catalog, sync_canonical_library
from src.services.query import query_brain
from src.services.reminders import store_reminder
from src.services.project_state import recompute_project_states
from src.services.secrets import build_secret_inventory, request_secret_challenge, reveal_secret_once, verify_secret_challenge
from src.services.session_bootstrap import (
    build_session_bootstrap,
    publish_curated_session_story,
    record_session_closeout,
)
from src.services.story import build_project_brief_payload, build_project_latest_closeout_payload, build_project_story_payload
from src.services.voice import refresh_voice_profile
from src.services.sync import import_collector_payload, record_sync_report, run_github_sync
from src.worker.main import enqueue_ingest

router = APIRouter(prefix="/api", tags=["brain"])


def _requester_identity(request: Request) -> str:
    session_scope = request.scope.get("session") or {}
    if session_scope.get("dashboard_username"):
        return f"dashboard:{session_scope['dashboard_username']}"
    return "api-client"


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/brain/self", dependencies=[Depends(require_api_token)])
async def brain_self_route() -> dict:
    async with async_session() as session:
        return await build_brain_self_description(session)


@router.get("/library", dependencies=[Depends(require_api_token)])
async def library_route(
    q: str | None = None,
    record_kind: str | None = None,
    facet: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    sync: bool = Query(default=False),
) -> dict:
    async with async_session() as session:
        if sync:
            await sync_canonical_library(session)
        items = await build_library_catalog(
            session,
            q=q,
            record_kind=record_kind,
            facet=facet,
            limit=limit,
        )
    return {
        "display_timezone": "America/New_York",
        "count": len(items),
        "items": items,
    }


@router.get("/threads", dependencies=[Depends(require_api_token)])
async def threads_route(
    q: str | None = None,
    thread_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    sync: bool = Query(default=False),
) -> dict:
    async with async_session() as session:
        if sync:
            await sync_canonical_library(session)
        records = await store.list_thread_records(session, thread_type=thread_type, q=q, limit=limit)
    return {
        "count": len(records),
        "items": [
            {
                "id": str(record.id),
                "thread_type": record.thread_type,
                "title": record.title,
                "summary": record.summary,
                "status": record.status,
                "priority": record.priority,
                "subject_ref": record.subject_ref,
                "aliases": record.aliases or [],
                "provenance_kind": record.provenance_kind,
                **human_datetime_payload(record.last_event_at, prefix="last_event_at"),
            }
            for record in records
        ],
    }


@router.get("/episodes", dependencies=[Depends(require_api_token)])
async def episodes_route(
    q: str | None = None,
    episode_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    sync: bool = Query(default=False),
) -> dict:
    async with async_session() as session:
        if sync:
            await sync_canonical_library(session)
        records = await store.list_episode_records(session, episode_type=episode_type, q=q, limit=limit)
    return {
        "count": len(records),
        "items": [
            {
                "id": str(record.id),
                "episode_type": record.episode_type,
                "title": record.title,
                "summary": record.summary,
                "participants": record.participants or [],
                "provenance_kind": record.provenance_kind,
                **human_datetime_payload(record.coverage_start, prefix="coverage_start"),
                **human_datetime_payload(record.coverage_end, prefix="coverage_end"),
            }
            for record in records
        ],
    }


@router.get("/entities", dependencies=[Depends(require_api_token)])
async def entities_route(
    q: str | None = None,
    entity_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    sync: bool = Query(default=False),
) -> dict:
    async with async_session() as session:
        if sync:
            await sync_canonical_library(session)
        records = await store.list_entity_records(session, entity_type=entity_type, q=q, limit=limit)
    return {
        "count": len(records),
        "items": [
            {
                "id": str(record.id),
                "entity_type": record.entity_type,
                "name": record.name,
                "summary": record.summary,
                "aliases": record.aliases or [],
                "thread_ids": record.thread_ids or [],
                **human_datetime_payload(record.last_seen_at, prefix="last_seen_at"),
            }
            for record in records
        ],
    }


@router.get("/syntheses", dependencies=[Depends(require_api_token)])
async def syntheses_route(
    q: str | None = None,
    synthesis_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
    sync: bool = Query(default=False),
) -> dict:
    async with async_session() as session:
        if sync:
            await sync_canonical_library(session)
        records = await store.list_synthesis_records(session, synthesis_type=synthesis_type, q=q, limit=limit)
    return {
        "count": len(records),
        "items": [
            {
                "id": str(record.id),
                "synthesis_type": record.synthesis_type,
                "title": record.title,
                "summary": record.summary,
                "certainty_class": record.certainty_class,
                "provenance_kind": record.provenance_kind,
                **human_datetime_payload(record.event_time, prefix="event_time"),
            }
            for record in records
        ],
    }


@router.get("/changes", dependencies=[Depends(require_api_token)])
async def changes_route(limit: int = Query(default=40, ge=1, le=100)) -> dict:
    async with async_session() as session:
        payload = await build_final_stored_data(session)
    return {
        "count": min(limit, len(payload["items"])),
        "items": payload["items"][:limit],
    }


@router.get("/coverage-gaps", dependencies=[Depends(require_api_token)])
async def coverage_gaps_route(sync: bool = Query(default=False)) -> dict:
    async with async_session() as session:
        if sync:
            await sync_canonical_library(session)
        threads = await store.list_thread_records(session, limit=300)
        observations = await store.list_observation_records(session, limit=400)
    empty_threads = [thread for thread in threads if not (thread.summary or "").strip()]
    weak_observations = [record for record in observations if float(record.certainty or 0.0) < 0.75]
    return {
        "thread_count": len(threads),
        "observation_count": len(observations),
        "threads_missing_summary": [
            {"id": str(thread.id), "title": thread.title, "thread_type": thread.thread_type}
            for thread in empty_threads[:30]
        ],
        "low_certainty_observations": [
            {"id": str(record.id), "title": record.title, "certainty": record.certainty}
            for record in weak_observations[:30]
        ],
    }


@router.post("/ingest/manual", dependencies=[Depends(require_api_token)])
async def ingest_manual(payload: ManualIngestRequest) -> dict:
    await enqueue_ingest(
        discord_message_id=None,
        discord_channel_id="api",
        text=payload.text,
        attachments=[],
        force_category=payload.category,
        source=payload.source,
    )
    return {"status": "queued", "source": payload.source}


@router.post("/ingest/collector", dependencies=[Depends(require_api_token)])
async def ingest_collector(payload: CollectorIngestRequest) -> dict:
    async with async_session() as session:
        result = await import_collector_payload(
            session,
            source_type=payload.source_type,
            source_name=payload.source_name,
            mode=payload.mode,
            device_name=payload.device_name,
            emit_sync_event=payload.emit_sync_event,
            entries=[entry.model_dump(mode="json") for entry in payload.entries],
        )
    return {"status": "completed", **result}


@router.post("/sync/run/{source}", dependencies=[Depends(require_api_token)], response_model=SyncRunResponse)
async def run_source_sync(source: str) -> SyncRunResponse:
    async with async_session() as session:
        if source == "github":
            result = await run_github_sync(session)
            return SyncRunResponse(**result)
        if source == "collector":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Collector sync is push-based. Use POST /api/ingest/collector.",
            )
        if source == "apple_notes":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Apple Notes sync is collector-based. Use POST /api/ingest/collector with source_type=apple_notes.",
            )
        if source in {"gmail", "drive", "google_keep"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google ingestion is not enabled in this build. Use local exports if you want a one-time import later.",
            )

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown source: {source}")


@router.post("/sync/report", dependencies=[Depends(require_api_token)], response_model=SyncRunResponse)
async def report_sync_run(payload: SyncReportRequest) -> SyncRunResponse:
    async with async_session() as session:
        result = await record_sync_report(
            session,
            source_type=payload.source_type,
            source_name=payload.source_name,
            mode=payload.mode,
            status=payload.status,
            items_seen=payload.items_seen,
            items_imported=payload.items_imported,
            device_name=payload.device_name,
            error=payload.error,
            metadata=payload.metadata,
        )
    return SyncRunResponse(**result)


@router.get("/projects/{project_id}/story", dependencies=[Depends(require_api_token)])
async def get_project_story_route(project_id: uuid.UUID) -> dict:
    async with async_session() as session:
        payload = await build_project_story_payload(session, project_id)
    if not payload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return payload


@router.get("/projects/{project_id}/brief", dependencies=[Depends(require_api_token)])
async def get_project_brief_route(project_id: uuid.UUID) -> dict:
    async with async_session() as session:
        payload = await build_project_brief_payload(session, project_id)
    if not payload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return payload


@router.get("/projects/{project_id}/latest-closeout", dependencies=[Depends(require_api_token)])
async def get_project_latest_closeout_route(project_id: uuid.UUID) -> dict:
    async with async_session() as session:
        payload = await build_project_latest_closeout_payload(session, project_id)
    if not payload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return payload


@router.get("/search", dependencies=[Depends(require_api_token)])
async def search_route(
    query: str = Query(min_length=3),
    category: str | None = None,
    limit: int = Query(default=10, ge=1, le=25),
    include_content: bool = False,
) -> list[dict]:
    query_embedding = await embed_text(query)
    async with async_session() as session:
        results = await vector_search(session, query_embedding, limit=limit, category=category)
        items = []
        for result in results:
            item = {
                "similarity": round(result["similarity"], 3),
                "chunk_preview": result["content"][:200] if not include_content else result["content"],
                "category": result.get("resolved_category"),
            }
            if result.get("note_id"):
                note = await get_note(session, result["note_id"])
                if note:
                    item.update({
                        "id": str(note.id),
                        "type": "note",
                        "title": note.title,
                        "status": note.status,
                    })
            items.append(item)
    return items


@router.post("/query", dependencies=[Depends(require_api_token)])
async def query_route(payload: QueryRequest) -> dict:
    async with async_session() as session:
        return await query_brain(
            session,
            question=payload.question,
            mode=payload.mode,
            category=payload.category,
            use_opus=payload.use_opus,
            include_web=payload.include_web,
        )


@router.post("/projects/recompute", dependencies=[Depends(require_api_token)])
async def recompute_projects_route(payload: ProjectStateRefreshRequest) -> dict:
    project_ids = [uuid.UUID(value) for value in payload.project_ids]
    async with async_session() as session:
        snapshots = await recompute_project_states(session, project_note_ids=project_ids or None)
    return {
        "status": "completed",
        "projects": [
            {
                "project_note_id": str(item.project_note_id),
                "status": item.status,
                "active_score": item.active_score,
            }
            for item in snapshots
        ],
    }


@router.post("/projects/manual-state", dependencies=[Depends(require_api_token)])
async def set_project_manual_state_route(payload: ProjectManualStateRequest) -> dict:
    if payload.manual_state not in PROJECT_MANUAL_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"manual_state must be one of: {', '.join(PROJECT_MANUAL_STATES)}",
        )
    async with async_session() as session:
        project = await resolve_project(
            session,
            project_hint=payload.project_name,
            source_refs=[payload.project_name],
            create_if_missing=False,
        )
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        snapshot = await store.set_project_manual_state(
            session,
            project_note_id=project.id,
            manual_state=payload.manual_state,
        )
        await recompute_project_states(session, project_note_ids=[project.id])
    return {
        "status": "updated",
        "project": project.title,
        "manual_state": snapshot.manual_state,
    }


@router.post("/reminders", dependencies=[Depends(require_api_token)])
async def create_reminder_route(payload: ReminderCreateRequest) -> dict:
    async with async_session() as session:
        note = await store.create_note(
            session,
            category="reminder",
            title=payload.text[:120],
            content=payload.text,
            priority="medium",
            discord_channel_id=payload.discord_channel_id,
        )
        project_note_id = None
        if payload.project_name:
            project = await resolve_project(
                session,
                project_hint=payload.project_name,
                source_refs=[payload.project_name],
                create_if_missing=False,
            )
            if project:
                project_note_id = project.id
        reminder = await store_reminder(
            session,
            raw_text=payload.text,
            note_id=note.id,
            project_note_id=project_note_id,
            discord_channel_id=payload.discord_channel_id,
        )
    return {
        "status": "stored",
        "reminder_id": str(reminder.id),
        "title": reminder.title,
        **human_datetime_payload(reminder.next_fire_at, prefix="next_fire_at", fallback="unscheduled"),
    }


@router.get("/reminders/due", dependencies=[Depends(require_api_token)])
async def list_due_reminders_route() -> list[dict]:
    from datetime import datetime, timezone

    async with async_session() as session:
        reminders = await store.list_due_reminders(session, due_before=datetime.now(timezone.utc), limit=50)
    return [
        {
            "id": str(item.id),
            "title": item.title,
            "status": item.status,
            **human_datetime_payload(item.next_fire_at, prefix="next_fire_at", fallback="unscheduled"),
        }
        for item in reminders
    ]


@router.post("/digest/morning", dependencies=[Depends(require_api_token)])
async def generate_morning_digest_route() -> dict:
    digest_date = datetime.now(ZoneInfo("America/New_York")).date()
    async with async_session() as session:
        payload = await generate_or_refresh_digest(session, digest_date=digest_date, trigger="manual")
    return payload


@router.post("/voice/refresh", dependencies=[Depends(require_api_token)])
async def refresh_voice_profile_route() -> dict:
    async with async_session() as session:
        return await refresh_voice_profile(session)


@router.post("/agent/session/bootstrap", dependencies=[Depends(require_api_token)])
async def agent_session_bootstrap_route(payload: AgentSessionBootstrapRequest) -> dict:
    async with async_session() as session:
        return await build_session_bootstrap(
            session,
            agent_kind=payload.agent_kind,
            session_id=payload.session_id,
            cwd=payload.cwd,
            project_hint=payload.project_hint,
            task_hint=payload.task_hint,
            include_web=payload.include_web,
        )


@router.post("/agent/session/closeout", dependencies=[Depends(require_api_token)])
async def agent_session_closeout_route(payload: AgentSessionCloseoutRequest) -> dict:
    async with async_session() as session:
        return await record_session_closeout(
            session,
            agent_kind=payload.agent_kind,
            session_id=payload.session_id,
            cwd=payload.cwd,
            project_ref=payload.project_ref,
            summary=payload.summary,
            decisions=payload.decisions,
            changes=payload.changes,
            open_questions=payload.open_questions,
            source_links=payload.source_links,
            transcript_excerpt=payload.transcript_excerpt,
        )


@router.post("/agent/session/story", dependencies=[Depends(require_api_token)])
async def agent_session_story_route(payload: AgentSessionStoryRequest) -> dict:
    async with async_session() as session:
        return await publish_curated_session_story(
            session,
            agent_kind=payload.agent_kind,
            session_id=payload.session_id,
            project_ref=payload.project_ref,
            title=payload.title,
            summary=payload.summary,
            direction=payload.direction,
            changes=payload.changes,
            open_loops=payload.open_loops,
            source_links=payload.source_links,
            transcript_excerpt=payload.transcript_excerpt,
            tags=payload.tags,
            actor_name=payload.actor_name,
        )


@router.get("/secrets", dependencies=[Depends(require_dashboard_token)])
async def list_secrets_route(request: Request) -> dict:
    async with async_session() as session:
        inventory = await build_secret_inventory(session)
    return {
        "requester": _requester_identity(request),
        "count": len(inventory),
        "items": inventory,
    }


@router.post("/secrets/challenge", dependencies=[Depends(require_dashboard_token)])
async def request_secret_challenge_route(request: Request, payload: SecretChallengeRequest) -> dict:
    async with async_session() as session:
        try:
            secret_id = uuid.UUID(payload.secret_id) if payload.secret_id else None
            return await request_secret_challenge(
                session,
                requester=_requester_identity(request),
                purpose=payload.purpose,
                secret_id=secret_id,
                alias=payload.alias,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/secrets/verify", dependencies=[Depends(require_dashboard_token)])
async def verify_secret_challenge_route(request: Request, payload: SecretVerifyRequest) -> dict:
    async with async_session() as session:
        try:
            return await verify_secret_challenge(
                session,
                requester=_requester_identity(request),
                challenge_id=uuid.UUID(payload.challenge_id),
                otp_code=payload.otp_code,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/secrets/{secret_id}/reveal", dependencies=[Depends(require_dashboard_token)])
async def reveal_secret_route(
    request: Request,
    secret_id: uuid.UUID,
    payload: SecretRevealRequest,
) -> dict:
    async with async_session() as session:
        try:
            return await reveal_secret_once(
                session,
                requester=_requester_identity(request),
                secret_id=secret_id,
                grant_token=payload.grant_token,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
