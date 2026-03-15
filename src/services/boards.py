"""Board generation for daily and weekly operating narratives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.config import settings
from src.lib import store
from src.lib.provenance import DERIVED_ENTRY_TYPES, signal_kind_for_artifact, signal_kind_for_event
from src.lib.time import describe_event_time, format_display_datetime


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
        if not cleaned or len(cleaned) < 4:
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


def _artifact_signal_kind(item: dict) -> str:
    artifact = item["artifact"]
    metadata = dict(getattr(artifact, "metadata_", {}) or {})
    return signal_kind_for_artifact(
        source=getattr(artifact, "source", None),
        capture_context=metadata.get("capture_context"),
    )


def _event_signal_kind(entry) -> str:
    return signal_kind_for_event(entry_type=getattr(entry, "entry_type", None), actor_type=getattr(entry, "actor_type", None))


def _included_reason_for_item(item: dict) -> str:
    category = item.get("category") or "note"
    intent = item.get("capture_intent") or "thought"
    if category in {"daily_planner", "weekly_planner"}:
        return "Validated planner capture from the board window."
    if category == "task":
        return "Validated task capture from the board window."
    if intent == "status_update":
        return "Direct status update captured in the board window."
    return "Validated direct capture from the board window."


def _excluded_reason_for_item(item: dict) -> str:
    if item.get("validation_status") != "validated":
        return "Excluded because the capture still needs review."
    if not item.get("eligible_for_boards"):
        return "Excluded because this capture is not eligible for boards."
    if _is_low_signal_board_item(item):
        return "Excluded because it is a low-signal repo/context snapshot."
    if _artifact_signal_kind(item) == "derived_system":
        return "Excluded because it is a derived system artifact, not direct work."
    return "Excluded by board filters."


def _project_titles_from_item(item: dict) -> list[str]:
    artifact = item["artifact"]
    metadata = dict(getattr(artifact, "metadata_", {}) or {})
    project_ref = metadata.get("project_ref")
    if project_ref:
        return [str(project_ref)]
    source_metadata = dict(metadata.get("source_metadata") or {})
    candidate = source_metadata.get("project_ref")
    return [str(candidate)] if candidate else []


def _event_reason(entry) -> str:
    entry_type = getattr(entry, "entry_type", "") or "story_event"
    if entry_type in {"session_closeout", "progress_update"}:
        return "Direct project progress signal."
    if entry_type == "conversation_session":
        return "Agent session activity tied to project work."
    if getattr(entry, "open_question", None):
        return "Direct event with an open loop still attached."
    return "Direct same-day story event."


def _direct_board_events(events: list) -> tuple[list, list]:
    direct_events = []
    derived_events = []
    for entry in events:
        if getattr(entry, "entry_type", "") in DERIVED_ENTRY_TYPES or _event_signal_kind(entry) == "derived_system":
            derived_events.append(entry)
            continue
        direct_events.append(entry)
    return direct_events, derived_events


def _truncate_lines(lines: list[str], *, limit: int = 8) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        cleaned = (line or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned[:180])
        if len(deduped) >= limit:
            break
    return deduped


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
    all_events = await store.list_story_events(
        session,
        since=window.coverage_start_utc,
        until=window.coverage_end_utc,
        limit=150,
        ascending=True,
    )

    included_items: list[dict] = []
    excluded_ids: list[str] = []
    included_source_reasons: list[dict] = []
    excluded_source_reasons: list[dict] = []

    for item in all_items:
        artifact = item["artifact"]
        artifact_id = str(artifact.id)
        event_info = describe_event_time(item.get("event_time") or getattr(artifact, "created_at", None))
        if (
            item.get("validation_status") != "validated"
            or not item.get("eligible_for_boards")
            or _is_low_signal_board_item(item)
            or _artifact_signal_kind(item) == "derived_system"
        ):
            excluded_ids.append(artifact_id)
            excluded_source_reasons.append(
                {
                    "artifact_id": artifact_id,
                    "title": artifact.summary or artifact.content_type,
                    "reason": _excluded_reason_for_item(item),
                    "signal_kind": _artifact_signal_kind(item),
                    **event_info,
                }
            )
            continue
        included_items.append(item)
        included_source_reasons.append(
            {
                "artifact_id": artifact_id,
                "title": artifact.summary or artifact.content_type,
                "reason": _included_reason_for_item(item),
                "signal_kind": _artifact_signal_kind(item),
                **event_info,
            }
        )

    source_ids = [str(item["artifact"].id) for item in included_items]
    direct_events, derived_events = _direct_board_events(all_events)

    project_signals: list[dict] = []
    seen_projects: set[str] = set()
    for entry in direct_events:
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
                "summary": summary[:180] or "Recent direct activity landed here.",
                "signal_kind": _event_signal_kind(entry),
                **describe_event_time(getattr(entry, "happened_at", None)),
            }
        )
        if len(project_signals) >= 6:
            break

    direct_capture_lines = [line for line in (_artifact_line(item) for item in included_items) if line]
    what_mattered = _truncate_lines(direct_capture_lines, limit=8)
    if not what_mattered:
        what_mattered = ["No validated captures landed in this window."]

    carry_forward: list[str] = []
    for item in included_items:
        if item["category"] in {"daily_planner", "weekly_planner", "task", "reminder"}:
            carry_forward.extend(_carry_forward_from_text(item["artifact"].raw_text or ""))
    carry_forward = carry_forward[:8]

    project_titles_from_captures = {
        title
        for item in included_items
        for title in _project_titles_from_item(item)
    }
    had_direct_project_work = bool(project_signals)
    had_project_capture_without_proof = bool(project_titles_from_captures) and not had_direct_project_work

    if had_direct_project_work:
        story = (
            f"{window.coverage_label} shows {len(included_items)} validated direct captures and "
            f"{len(project_signals)} direct project signals. The clearest threads were: {'; '.join(what_mattered[:3])}."
        )
    elif included_items:
        story = (
            f"{window.coverage_label} has no direct project work evidence. "
            f"The day mainly captured plans, notes, or reference material: {'; '.join(what_mattered[:3])}."
        )
        if had_project_capture_without_proof:
            story += " Projects were mentioned, but the evidence does not show direct progress landed that day."
    else:
        story = f"{window.coverage_label} has no validated direct activity in the brain."

    payload = {
        "board_type": window.board_type,
        "generated_for_date": window.generated_for_date.isoformat(),
        "coverage_start": window.coverage_start_local.isoformat(),
        "coverage_end": window.coverage_end_local.isoformat(),
        "coverage_label": window.coverage_label,
        "display_timezone": settings.digest_timezone,
        "story": story,
        "summary": story,
        "what_mattered": what_mattered[:8],
        "carry_forward": carry_forward[:8],
        "project_signals": project_signals[:6],
        "source_count": len(source_ids),
        "excluded_count": len(excluded_ids),
        "direct_source_count": len(source_ids) + len(direct_events),
        "derived_source_count": len(derived_events),
        "included_source_reasons": included_source_reasons[:50],
        "excluded_source_reasons": excluded_source_reasons[:50],
        "derived_insights": [
            {
                "title": entry.title,
                "summary": entry.summary,
                "reason": "Derived system signal kept out of the main board narrative.",
                "signal_kind": _event_signal_kind(entry),
                **describe_event_time(getattr(entry, "happened_at", None)),
            }
            for entry in derived_events[:8]
        ],
        "had_direct_project_work": had_direct_project_work,
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
