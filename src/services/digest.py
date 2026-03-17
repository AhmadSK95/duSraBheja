"""Simplified daily digest built from the latest daily board and current project state."""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from src.config import settings
from src.lib import store
from src.lib.time import human_datetime_text
from src.services.boards import daily_board_window, generate_or_refresh_board
from src.services.project_state import recompute_project_states


def _shorten(value: str | None, limit: int = 220) -> str:
    text = " ".join((value or "").split()).strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def _project_status_item(project_title: str, snapshot, board_signal: str | None) -> dict:
    blockers = list(getattr(snapshot, "blockers", []) or [])
    holes = list(getattr(snapshot, "holes", []) or [])
    blocked_or_unclear = blockers[0] if blockers else (holes[0] if holes else "No blocker is explicitly logged.")
    best_next_move = _shorten(getattr(snapshot, "remaining", None), 180) or f"Define the next concrete move for {project_title}."
    return {
        "project": project_title,
        "where_it_stands": _shorten(getattr(snapshot, "implemented", None), 180) or "State still needs a better canonical summary.",
        "what_changed": _shorten(board_signal or getattr(snapshot, "what_changed", None), 180) or "No fresh validated change signal.",
        "blocked_or_unclear": _shorten(blocked_or_unclear, 180),
        "best_next_move": best_next_move,
    }


async def build_daily_digest_payload(session, *, digest_date: date, trigger: str = "scheduled") -> dict:
    board_date = digest_date - timedelta(days=1)
    board = await store.get_latest_board(session, board_type="daily", generated_for_date=board_date)
    if not board:
        window = daily_board_window(board_date)
        payload = await generate_or_refresh_board(session, window=window)
        board = await store.get_latest_board(session, board_type="daily", generated_for_date=board_date)
    board_payload = dict((board.payload if board else {}) or {})

    await recompute_project_states(session)
    snapshots = await store.list_project_state_snapshots(session, limit=20)
    reminder_zone = ZoneInfo(settings.digest_timezone)
    reminders = await store.list_reminders(session, status="active", limit=50)
    tasks = await store.list_notes(session, category="task", limit=12)

    project_signal_map = {
        item.get("project"): item.get("summary")
        for item in board_payload.get("project_signals", [])
        if item.get("project")
    }

    project_status: list[dict] = []
    for snapshot in snapshots:
        if snapshot.status not in {"active", "warming_up", "blocked"} and snapshot.manual_state != "pinned":
            continue
        project = await store.get_note(session, snapshot.project_note_id)
        if not project:
            continue
        project_status.append(
            _project_status_item(project.title, snapshot, project_signal_map.get(project.title))
        )
        if len(project_status) >= 5:
            break

    possible_tasks: list[dict] = []
    for item in board_payload.get("carry_forward", [])[:6]:
        possible_tasks.append({"title": item, "why": "Carried forward from the latest validated daily board."})
    for task in tasks:
        if len(possible_tasks) >= 8:
            break
        possible_tasks.append(
            {
                "title": task.title,
                "why": f"Existing task note with priority {getattr(task, 'priority', 'medium')}.",
            }
        )
    for item in project_status:
        if len(possible_tasks) >= 8:
            break
        possible_tasks.append(
            {
                "title": f"{item['project']}: {item['best_next_move']}",
                "why": "Derived from current validated project state.",
            }
        )

    reminders_due_today = [
        {
            "id": str(reminder.id),
            "title": reminder.title,
            "next_fire_at": human_datetime_text(reminder.next_fire_at, fallback="unscheduled"),
        }
        for reminder in reminders
        if reminder.next_fire_at and reminder.next_fire_at.astimezone(reminder_zone).date() == digest_date
    ][:10]

    summary = board_payload.get("story") or "Morning operating brief grounded in the latest daily board."
    return {
        "digest_date": digest_date.isoformat(),
        "board_date": board_date.isoformat(),
        "headline": f"{digest_date.isoformat()} operating brief",
        "summary": _shorten(summary, 1200),
        "project_status": project_status,
        "possible_tasks": possible_tasks[:8],
        "reminders_due_today": reminders_due_today,
        "trigger": trigger,
    }


async def generate_or_refresh_digest(session, *, digest_date: date, trigger: str = "scheduled") -> dict:
    payload = await build_daily_digest_payload(session, digest_date=digest_date, trigger=trigger)
    existing = await store.get_digest_by_date(session, digest_date)
    if existing:
        existing.payload = payload
        await session.commit()
        await session.refresh(existing)
        return payload

    await store.create_digest(session, digest_date=digest_date, payload=payload)
    return payload
