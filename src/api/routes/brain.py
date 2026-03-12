"""Primary API routes for the private brain service."""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.schemas import (
    CollectorIngestRequest,
    ManualIngestRequest,
    ProjectStateRefreshRequest,
    QueryRequest,
    ReminderCreateRequest,
    SyncReportRequest,
    SyncRunResponse,
)
from src.database import async_session
from src.lib.auth import require_api_token
from src.lib.embeddings import embed_text
from src.lib.store import get_note, vector_search
from src.lib import store
from src.services.digest import generate_or_refresh_digest
from src.services.query import query_brain
from src.services.reminders import store_reminder
from src.services.project_state import recompute_project_states
from src.services.story import build_project_brief_payload, build_project_story_payload
from src.services.sync import import_collector_payload, record_sync_report, run_github_sync
from src.worker.main import enqueue_ingest

router = APIRouter(prefix="/api", tags=["brain"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


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
async def get_project_story_route(project_id: str) -> dict:
    async with async_session() as session:
        payload = await build_project_story_payload(session, uuid.UUID(project_id))
    if not payload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return payload


@router.get("/projects/{project_id}/brief", dependencies=[Depends(require_api_token)])
async def get_project_brief_route(project_id: str) -> dict:
    async with async_session() as session:
        payload = await build_project_brief_payload(session, uuid.UUID(project_id))
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
            matches = await store.find_notes_by_title(session, payload.project_name, "project")
            if matches:
                project_note_id = matches[0].id
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
        "next_fire_at": str(reminder.next_fire_at) if reminder.next_fire_at else None,
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
            "next_fire_at": str(item.next_fire_at) if item.next_fire_at else None,
            "status": item.status,
        }
        for item in reminders
    ]


@router.post("/digest/morning", dependencies=[Depends(require_api_token)])
async def generate_morning_digest_route() -> dict:
    digest_date = datetime.now(ZoneInfo("America/New_York")).date()
    async with async_session() as session:
        payload = await generate_or_refresh_digest(session, digest_date=digest_date, trigger="manual")
    return payload
