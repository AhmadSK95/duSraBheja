"""Primary API routes for the private brain service."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.schemas import CollectorIngestRequest, ManualIngestRequest, SyncRunResponse
from src.database import async_session
from src.lib.auth import require_api_token
from src.lib.embeddings import embed_text
from src.lib.store import get_note, vector_search
from src.services.story import build_project_brief_payload, build_project_story_payload
from src.services.sync import import_collector_payload, run_github_sync
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
            source_name=payload.source_name,
            mode=payload.mode,
            device_name=payload.device_name,
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
