"""Private dashboard and moderation routes."""

from __future__ import annotations

import html
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse

from src.api.schemas import ArtifactModerationRequest, BoardRegenerateRequest
from src.constants import normalize_category
from src.database import async_session
from src.lib import store
from src.lib.auth import require_api_token, require_dashboard_token
from src.services.boards import daily_board_window, generate_or_refresh_board, weekly_board_window
from src.services.capture_analysis import normalize_capture_intent, normalize_validation_status
from src.services.digest import generate_or_refresh_digest
from src.services.project_state import recompute_project_states
from src.worker.main import JOB_GENERATE_EMBEDDINGS, JOB_PROCESS_LIBRARIAN, get_pool

router = APIRouter(tags=["dashboard"])
api_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _page(title: str, body: str, *, token: str) -> HTMLResponse:
    safe_token = html.escape(token)
    return HTMLResponse(
        f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>{html.escape(title)}</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; background: #f7f5ef; color: #1f2937; }}
      a {{ color: #0f766e; text-decoration: none; }}
      table {{ width: 100%; border-collapse: collapse; margin-top: 16px; background: white; }}
      th, td {{ border: 1px solid #d6d3d1; padding: 10px; vertical-align: top; text-align: left; }}
      th {{ background: #f1f5f9; }}
      .nav a {{ margin-right: 16px; font-weight: 600; }}
      .pill {{ display: inline-block; padding: 2px 8px; border-radius: 999px; background: #e2e8f0; margin-right: 6px; font-size: 12px; }}
      pre {{ white-space: pre-wrap; background: #111827; color: #f9fafb; padding: 12px; border-radius: 8px; }}
    </style>
  </head>
  <body>
    <div class="nav">
      <a href="/dashboard/artifacts?token={safe_token}">Artifacts</a>
      <a href="/dashboard/review?token={safe_token}">Review</a>
      <a href="/dashboard/boards?token={safe_token}">Boards</a>
      <a href="/dashboard/sync-health?token={safe_token}">Sync Health</a>
    </div>
    {body}
  </body>
</html>"""
    )


def _render_artifact_rows(items: list[dict], token: str) -> str:
    rows = []
    for item in items:
        artifact = item["artifact"]
        issues = ", ".join(issue.get("code", "issue") for issue in item.get("quality_issues", [])) or "none"
        rows.append(
            "<tr>"
            f"<td><a href=\"/dashboard/artifacts/{artifact.id}?token={html.escape(token)}\">{str(artifact.id)[:8]}</a></td>"
            f"<td>{html.escape(artifact.source)}</td>"
            f"<td>{html.escape(item.get('category') or 'unclassified')}</td>"
            f"<td>{html.escape(item.get('capture_intent') or 'unknown')}</td>"
            f"<td>{html.escape(item.get('validation_status') or 'unknown')}</td>"
            f"<td>{html.escape(issues)}</td>"
            f"<td>{artifact.created_at}</td>"
            "</tr>"
        )
    return "".join(rows)


@router.get("/dashboard", dependencies=[Depends(require_dashboard_token)])
async def dashboard_root(token: str = Query(default="")) -> RedirectResponse:
    return RedirectResponse(url=f"/dashboard/artifacts?token={token}", status_code=status.HTTP_302_FOUND)


@router.get("/dashboard/artifacts", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_artifacts(
    token: str = Query(default=""),
    validation_status: str | None = Query(default=None),
) -> HTMLResponse:
    async with async_session() as session:
        items = await store.list_artifact_interpretations(session, validation_status=validation_status, limit=100)
    body = (
        "<h1>Artifact Intake</h1>"
        "<p>Latest stored captures with their current interpretation and review status.</p>"
        "<table><thead><tr><th>ID</th><th>Source</th><th>Category</th><th>Intent</th><th>Validation</th><th>Issues</th><th>Created</th></tr></thead><tbody>"
        + _render_artifact_rows(items, token)
        + "</tbody></table>"
    )
    return _page("Artifacts", body, token=token)


@router.get("/dashboard/artifacts/{artifact_id}", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_artifact_detail(artifact_id: uuid.UUID, token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        item = await store.get_artifact_interpretation(session, artifact_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
        reviews = [review for review in await store.get_pending_reviews(session) if review.artifact_id == artifact_id]
    artifact = item["artifact"]
    issue_lines = "".join(
        f"<li>{html.escape(issue.get('code', 'issue'))}: {html.escape(issue.get('message', ''))}</li>"
        for issue in item.get("quality_issues", [])
    ) or "<li>None</li>"
    body = f"""
    <h1>Artifact {artifact.id}</h1>
    <p><span class="pill">{html.escape(item.get('category') or 'unclassified')}</span>
    <span class="pill">{html.escape(item.get('capture_intent') or 'unknown')}</span>
    <span class="pill">{html.escape(item.get('validation_status') or 'unknown')}</span></p>
    <h2>Quality Issues</h2>
    <ul>{issue_lines}</ul>
    <h2>Pending Review Prompts</h2>
    <ul>{"".join(f"<li>{html.escape(review.question)}</li>" for review in reviews) or "<li>None</li>"}</ul>
    <h2>Raw Capture</h2>
    <pre>{html.escape((artifact.raw_text or '')[:12000])}</pre>
    """
    return _page("Artifact Detail", body, token=token)


@router.get("/dashboard/review", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_review(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        reviews = await store.get_pending_reviews(session)
    rows = "".join(
        "<tr>"
        f"<td>{str(review.id)[:8]}</td>"
        f"<td>{html.escape(review.review_kind)}</td>"
        f"<td>{html.escape(review.question)}</td>"
        f"<td>{review.created_at}</td>"
        "</tr>"
        for review in reviews
    )
    body = (
        "<h1>Review Queue</h1>"
        "<table><thead><tr><th>ID</th><th>Kind</th><th>Prompt</th><th>Created</th></tr></thead><tbody>"
        + (rows or "<tr><td colspan='4'>No pending review items.</td></tr>")
        + "</tbody></table>"
    )
    return _page("Review Queue", body, token=token)


@router.get("/dashboard/boards", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_boards(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        boards = await store.list_boards(session, limit=30)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(board.board_type)}</td>"
        f"<td>{board.generated_for_date}</td>"
        f"<td>{board.coverage_start} -> {board.coverage_end}</td>"
        f"<td>{board.status}</td>"
        f"<td>{len(board.source_artifact_ids or [])}</td>"
        f"<td>{len(board.excluded_artifact_ids or [])}</td>"
        "</tr>"
        for board in boards
    )
    body = (
        "<h1>Boards</h1>"
        "<table><thead><tr><th>Type</th><th>For Date</th><th>Coverage</th><th>Status</th><th>Sources</th><th>Excluded</th></tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )
    return _page("Boards", body, token=token)


@router.get("/dashboard/sync-health", dependencies=[Depends(require_dashboard_token)], response_class=HTMLResponse)
async def dashboard_sync_health(token: str = Query(default="")) -> HTMLResponse:
    async with async_session() as session:
        runs = await store.list_recent_sync_runs(session, limit=50)
    rows = "".join(
        "<tr>"
        f"<td>{run.sync_source_id}</td>"
        f"<td>{html.escape(run.mode)}</td>"
        f"<td>{html.escape(run.status)}</td>"
        f"<td>{run.items_seen}</td>"
        f"<td>{run.items_imported}</td>"
        f"<td>{run.started_at}</td>"
        "</tr>"
        for run in runs
    )
    body = (
        "<h1>Sync Health</h1>"
        "<table><thead><tr><th>Source</th><th>Mode</th><th>Status</th><th>Seen</th><th>Imported</th><th>Started</th></tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )
    return _page("Sync Health", body, token=token)


@api_router.get("/artifacts", dependencies=[Depends(require_api_token)])
async def list_dashboard_artifacts(validation_status: str | None = None) -> list[dict]:
    async with async_session() as session:
        items = await store.list_artifact_interpretations(session, validation_status=validation_status, limit=100)
    return [
        {
            "artifact_id": str(item["artifact"].id),
            "source": item["artifact"].source,
            "category": item.get("category"),
            "capture_intent": item.get("capture_intent"),
            "validation_status": item.get("validation_status"),
            "quality_issues": item.get("quality_issues", []),
            "created_at": item["artifact"].created_at.isoformat(),
        }
        for item in items
    ]


@api_router.get("/artifacts/{artifact_id}", dependencies=[Depends(require_api_token)])
async def get_dashboard_artifact(artifact_id: uuid.UUID) -> dict:
    async with async_session() as session:
        item = await store.get_artifact_interpretation(session, artifact_id)
        if not item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    artifact = item["artifact"]
    return {
        "artifact_id": str(artifact.id),
        "source": artifact.source,
        "raw_text": artifact.raw_text,
        "category": item.get("category"),
        "capture_intent": item.get("capture_intent"),
        "validation_status": item.get("validation_status"),
        "quality_issues": item.get("quality_issues", []),
    }


@api_router.post("/artifacts/{artifact_id}/moderate", dependencies=[Depends(require_api_token)])
async def moderate_artifact(artifact_id: uuid.UUID, payload: ArtifactModerationRequest) -> dict:
    async with async_session() as session:
        latest = await store.get_latest_classification(session, artifact_id)
        if not latest:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Classification not found")
        values = {}
        if payload.category:
            values["category"] = normalize_category(payload.category, default=latest.category)
        if payload.capture_intent:
            values["capture_intent"] = normalize_capture_intent(payload.capture_intent, default=latest.capture_intent)
        if payload.validation_status:
            values["validation_status"] = normalize_validation_status(
                payload.validation_status,
                default=latest.validation_status,
            )
        if payload.quality_issues:
            values["quality_issues"] = payload.quality_issues
        if payload.eligible_for_boards is not None:
            values["eligible_for_boards"] = payload.eligible_for_boards
        if payload.eligible_for_project_state is not None:
            values["eligible_for_project_state"] = payload.eligible_for_project_state
        if values.get("validation_status", latest.validation_status) == "validated":
            values["is_final"] = True
        classification = await store.update_classification(session, latest.id, **values)
        for review in [review for review in await store.get_pending_reviews(session) if review.artifact_id == artifact_id]:
            await store.moderate_review(
                session,
                review.id,
                status="resolved",
                resolution=payload.moderation_notes or "Moderated from dashboard API.",
                moderation_notes=payload.moderation_notes,
                resolved_by=payload.resolved_by or "dashboard",
            )
        if classification and classification.is_final:
            pool = await get_pool()
            await pool.enqueue_job(JOB_GENERATE_EMBEDDINGS, artifact_id=str(artifact_id))
            await pool.enqueue_job(
                JOB_PROCESS_LIBRARIAN,
                artifact_id=str(artifact_id),
                classification_id=str(classification.id),
            )
        await recompute_project_states(session)
    return {
        "status": "ok",
        "artifact_id": str(artifact_id),
        "classification_id": str(classification.id) if classification else None,
    }


@api_router.get("/reviews", dependencies=[Depends(require_api_token)])
async def list_dashboard_reviews() -> list[dict]:
    async with async_session() as session:
        reviews = await store.get_pending_reviews(session)
    return [
        {
            "review_id": str(review.id),
            "artifact_id": str(review.artifact_id),
            "review_kind": review.review_kind,
            "question": review.question,
            "created_at": review.created_at.isoformat(),
        }
        for review in reviews
    ]


@api_router.get("/boards", dependencies=[Depends(require_api_token)])
async def list_dashboard_boards(board_type: str | None = None) -> list[dict]:
    async with async_session() as session:
        boards = await store.list_boards(session, board_type=board_type, limit=50)
    return [
        {
            "board_id": str(board.id),
            "board_type": board.board_type,
            "generated_for_date": board.generated_for_date.isoformat(),
            "coverage_start": board.coverage_start.isoformat(),
            "coverage_end": board.coverage_end.isoformat(),
            "status": board.status,
            "source_artifact_ids": board.source_artifact_ids or [],
            "excluded_artifact_ids": board.excluded_artifact_ids or [],
        }
        for board in boards
    ]


@api_router.post("/boards/regenerate", dependencies=[Depends(require_api_token)])
async def regenerate_board_route(payload: BoardRegenerateRequest) -> dict:
    target = date.fromisoformat(payload.target_date)
    async with async_session() as session:
        if payload.board_type == "daily":
            board_payload = await generate_or_refresh_board(session, window=daily_board_window(target))
        elif payload.board_type == "weekly":
            board_payload = await generate_or_refresh_board(session, window=weekly_board_window(target))
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported board type")
        await recompute_project_states(session)
        if payload.board_type == "daily":
            digest_payload = await generate_or_refresh_digest(session, digest_date=target + timedelta(days=1))
        else:
            digest_payload = None
    return {
        "status": "ok",
        "board": board_payload,
        "digest": digest_payload,
    }


@api_router.get("/sync-health", dependencies=[Depends(require_api_token)])
async def sync_health_route() -> list[dict]:
    async with async_session() as session:
        runs = await store.list_recent_sync_runs(session, limit=50)
    return [
        {
            "sync_run_id": str(run.id),
            "sync_source_id": str(run.sync_source_id),
            "mode": run.mode,
            "status": run.status,
            "items_seen": run.items_seen,
            "items_imported": run.items_imported,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
        for run in runs
    ]
