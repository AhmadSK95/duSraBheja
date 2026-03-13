"""Board generation for daily and weekly operating narratives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.config import settings
from src.lib import store


@dataclass(frozen=True)
class BoardWindow:
    board_type: str
    generated_for_date: date
    coverage_start_local: datetime
    coverage_end_local: datetime
    coverage_start_utc: datetime
    coverage_end_utc: datetime
    coverage_label: str


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.digest_timezone)


def _end_of_day(value: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(value, time.max, tzinfo=tz)


def daily_board_window(board_date: date) -> BoardWindow:
    tz = _tz()
    start_local = datetime.combine(board_date, time.min, tzinfo=tz)
    end_local = _end_of_day(board_date, tz)
    return BoardWindow(
        board_type="daily",
        generated_for_date=board_date,
        coverage_start_local=start_local,
        coverage_end_local=end_local,
        coverage_start_utc=start_local.astimezone(ZoneInfo("UTC")),
        coverage_end_utc=end_local.astimezone(ZoneInfo("UTC")),
        coverage_label=board_date.strftime("%A, %b %d, %Y"),
    )


def weekly_board_window(anchor_date: date) -> BoardWindow:
    tz = _tz()
    week_end = anchor_date
    week_start = week_end - timedelta(days=6)
    start_local = datetime.combine(week_start, time.min, tzinfo=tz)
    end_local = _end_of_day(week_end, tz)
    return BoardWindow(
        board_type="weekly",
        generated_for_date=week_end,
        coverage_start_local=start_local,
        coverage_end_local=end_local,
        coverage_start_utc=start_local.astimezone(ZoneInfo("UTC")),
        coverage_end_utc=end_local.astimezone(ZoneInfo("UTC")),
        coverage_label=f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d, %Y')}",
    )


def previous_daily_board_window(run_date: date) -> BoardWindow:
    return daily_board_window(run_date - timedelta(days=1))


def previous_weekly_board_window(run_date: date) -> BoardWindow:
    previous_sunday = run_date - timedelta(days=run_date.weekday() + 1)
    return weekly_board_window(previous_sunday)


def _artifact_line(item: dict) -> str | None:
    artifact = item["artifact"]
    summary = (artifact.summary or artifact.raw_text or "").strip()
    if not summary:
        return None
    return summary[:180]


def _is_low_signal_board_item(item: dict) -> bool:
    artifact = item["artifact"]
    source = getattr(artifact, "source", None)
    metadata = dict(getattr(artifact, "metadata_", {}) or {})
    source_metadata = dict(metadata.get("source_metadata") or {})
    tags = set(item.get("tags") or [])
    snapshot_kind = source_metadata.get("snapshot_kind") or metadata.get("snapshot_kind")
    entry_type = metadata.get("entry_type")

    if source in {"collector", "github"} and snapshot_kind in {"repo", "context_workspace", "directory_inventory"}:
        return True
    if source == "collector" and entry_type in {"context_dump", "context_signal_dump"}:
        return True
    if {"repo-snapshot", "inventory", "local-context"} & tags:
        return True
    summary = (artifact.summary or "").lower()
    if source == "collector" and ("local snapshot" in summary or "agent context snapshot" in summary):
        return True
    return False


def _carry_forward_from_text(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in (text or "").splitlines():
        cleaned = raw_line.strip().lstrip("-*•→").strip()
        if not cleaned:
            continue
        if len(cleaned) < 4:
            continue
        lines.append(cleaned[:160])
    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(line)
    return deduped[:8]


async def build_board_payload(session, *, window: BoardWindow) -> tuple[dict, list[str], list[str]]:
    validated_candidates = await store.list_artifacts_for_window(
        session,
        start=window.coverage_start_utc,
        end=window.coverage_end_utc,
        validation_status="validated",
        eligible_for_boards=True,
    )
    all_items = await store.list_artifacts_for_window(
        session,
        start=window.coverage_start_utc,
        end=window.coverage_end_utc,
    )
    low_signal_excluded = [str(item["artifact"].id) for item in validated_candidates if _is_low_signal_board_item(item)]
    validated = [item for item in validated_candidates if not _is_low_signal_board_item(item)]
    excluded_ids = [
        str(item["artifact"].id)
        for item in all_items
        if item["validation_status"] != "validated" or not item["eligible_for_boards"]
    ] + low_signal_excluded
    source_ids = [str(item["artifact"].id) for item in validated]

    recent_activity = await store.list_story_events(
        session,
        since=window.coverage_start_utc,
        until=window.coverage_end_utc,
        limit=100,
        ascending=True,
    )

    what_mattered = [line for line in (_artifact_line(item) for item in validated) if line][:8]
    if not what_mattered:
        what_mattered = ["No validated captures landed in this window."]

    carry_forward: list[str] = []
    for item in validated:
        if item["category"] in {"daily_planner", "weekly_planner", "task", "reminder"}:
            carry_forward.extend(_carry_forward_from_text(item["artifact"].raw_text or ""))
    carry_forward = carry_forward[:8]

    project_signals: list[dict] = []
    seen_projects: set[str] = set()
    for entry in recent_activity:
        if not getattr(entry, "project_note_id", None):
            continue
        project = await store.get_note(session, entry.project_note_id)
        if not project or str(project.id) in seen_projects:
            continue
        seen_projects.add(str(project.id))
        summary = (getattr(entry, "summary", None) or getattr(entry, "title", None) or "").strip()
        project_signals.append(
            {
                "project": project.title,
                "summary": summary[:180] or "Recent validated activity landed here.",
            }
        )
        if len(project_signals) >= 6:
            break

    activity_count = len(recent_activity)
    story = (
        f"{window.coverage_label} produced {len(validated)} validated captures and {activity_count} durable story signals. "
        f"The strongest threads were: {'; '.join(what_mattered[:3])}."
    )

    payload = {
        "board_type": window.board_type,
        "generated_for_date": window.generated_for_date.isoformat(),
        "coverage_start": window.coverage_start_local.isoformat(),
        "coverage_end": window.coverage_end_local.isoformat(),
        "coverage_label": window.coverage_label,
        "story": story,
        "summary": story,
        "what_mattered": what_mattered[:8],
        "carry_forward": carry_forward[:8],
        "project_signals": project_signals[:6],
        "source_count": len(source_ids),
        "excluded_count": len(excluded_ids),
    }
    return payload, source_ids, excluded_ids


async def generate_or_refresh_board(session, *, window: BoardWindow) -> dict:
    payload, source_ids, excluded_ids = await build_board_payload(session, window=window)
    board = await store.upsert_board(
        session,
        board_type=window.board_type,
        generated_for_date=window.generated_for_date,
        coverage_start=window.coverage_start_utc,
        coverage_end=window.coverage_end_utc,
        payload=payload,
        source_artifact_ids=source_ids,
        excluded_artifact_ids=excluded_ids,
        discord_channel_name=(
            settings.daily_board_channel_name if window.board_type == "daily" else settings.weekly_board_channel_name
        ),
    )
    payload["board_id"] = str(board.id)
    return payload
