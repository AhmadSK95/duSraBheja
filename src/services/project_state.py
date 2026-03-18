"""Project activity scoring, snapshot synthesis, and associative connections."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.storyteller import assess_project_state
from src.lib import store
from src.lib.time import human_datetime_text
from src.services.identity import is_low_signal_project_name

RECENT_WINDOW_DAYS = 14
DORMANT_WINDOW_DAYS = 30
MAX_ASSESSED_PROJECTS = 5
LOW_SIGNAL_ENTRY_TYPES = {
    "context_dump",
    "context_signal_dump",
    "directory_inventory",
    "repo_snapshot",
    "repo_signal_summary",
    "workspace_signal_summary",
    "workspace_landscape_summary",
    "agent_memory_snapshot",
    "plan_snapshot",
    "todo_snapshot",
    "agent_reference_signal",
    "agent_plan_signal",
    "agent_todo_signal",
}
INDIRECT_ACTIVITY_ENTRY_TYPES = {"knowledge_refresh", "voice_refresh"}
DIRECT_STATE_ENTRY_TYPES = {"session_closeout", "progress_update", "decision", "conversation_session"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _extract_iso(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _recency_weight(value: datetime | None, *, now: datetime) -> float:
    if not value:
        return 0.2
    age = now - value
    if age <= timedelta(days=1):
        return 1.0
    if age <= timedelta(days=3):
        return 0.75
    if age <= timedelta(days=7):
        return 0.45
    if age <= timedelta(days=RECENT_WINDOW_DAYS):
        return 0.2
    return 0.05


def _session_signal_at(session_item) -> datetime | None:
    return (
        getattr(session_item, "ended_at", None)
        or getattr(session_item, "started_at", None)
        or getattr(session_item, "updated_at", None)
    )


def _project_event_activity_weight(entry) -> float:
    entry_type = getattr(entry, "entry_type", "") or ""
    actor_type = getattr(entry, "actor_type", "") or ""

    if entry_type in LOW_SIGNAL_ENTRY_TYPES:
        return 0.0
    if entry_type in {"conversation_session", "session_closeout", "progress_update", "decision"}:
        return 1.0
    if entry_type in {"research_thread", "synapse", "blind_spot"}:
        return 0.75 if actor_type in {"agent", "system"} else 0.55
    if entry_type in INDIRECT_ACTIVITY_ENTRY_TYPES:
        return 0.15 if actor_type in {"connector", "system"} else 0.35
    if getattr(entry, "open_question", None) or getattr(entry, "decision", None) or getattr(entry, "impact", None) or getattr(entry, "outcome", None):
        return 0.7
    if actor_type == "agent":
        return 0.65
    return 0.2


def _event_signal_at(entry) -> datetime | None:
    return getattr(entry, "happened_at", None)


def _project_event_priority(entry) -> tuple[int, datetime]:
    entry_type = getattr(entry, "entry_type", "") or ""
    signal_at = _event_signal_at(entry) or datetime(1970, 1, 1, tzinfo=timezone.utc)
    if entry_type == "session_closeout":
        return (5, signal_at)
    if entry_type in {"progress_update", "decision", "conversation_session"}:
        return (4, signal_at)
    if entry_type in {"research_thread", "blind_spot", "synapse"}:
        return (3, signal_at)
    return (1, signal_at)


def _prioritize_project_events(events: list) -> list:
    return sorted(events, key=_project_event_priority, reverse=True)


def _dedupe_nonempty(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _source_item_entry_type(item) -> str:
    payload = getattr(item, "payload", None) or {}
    return str(payload.get("entry_type") or "")


def _source_item_is_meaningful(item) -> bool:
    payload = getattr(item, "payload", None) or {}
    entry_type = _source_item_entry_type(item)
    if entry_type in LOW_SIGNAL_ENTRY_TYPES:
        return False
    if payload.get("eligible_for_project_state") is False:
        return False
    metadata = payload.get("metadata") or {}
    if (metadata.get("snapshot_kind") or "").lower() in {"repo", "directory_inventory", "repo_signal", "workspace_landscape", "context_workspace_signal"}:
        return False
    tags = {str(tag).lower() for tag in payload.get("tags") or []}
    if {"repo-snapshot", "inventory", "workspace-landscape"} & tags:
        return False
    return True


def _compact_text(value: str | None, *, limit: int = 280) -> str | None:
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _extract_markdown_section(value: str | None, heading: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    wanted = heading.strip().lower()
    lines = text.splitlines()
    collecting = False
    collected: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        lowered = stripped.lower()
        if lowered in {f"## {wanted}", f"### {wanted}"}:
            collecting = True
            continue
        if collecting and stripped.startswith("#"):
            break
        if collecting and stripped:
            collected.append(stripped.lstrip("- ").strip())
    return _compact_text(" ".join(collected)) if collected else None


def _preferred_event_summary(entry) -> str | None:
    summary = getattr(entry, "summary", None)
    body_markdown = getattr(entry, "body_markdown", None)
    extracted = _extract_markdown_section(summary, "Summary") or _extract_markdown_section(body_markdown, "Summary")
    if extracted:
        return extracted
    cleaned_summary = _compact_text(summary)
    if cleaned_summary and not cleaned_summary.startswith("# "):
        return cleaned_summary
    return _compact_text(getattr(entry, "outcome", None)) or _compact_text(getattr(entry, "title", None))


@dataclass
class ProjectMetrics:
    project: object
    snapshot: object | None
    events: list
    sessions: list
    repos: list
    source_items: list
    planners: list
    reminders: list
    feature_scores: dict
    active_score: float
    status: str
    last_signal_at: datetime | None
    blockers: list[str]
    why_active: str
    why_not_active: str


def _status_from_score(
    *,
    score: float,
    manual_state: str,
    blockers: list[str],
    last_signal_at: datetime | None,
    now: datetime,
) -> str:
    if manual_state == "ignored":
        return "dormant"
    if manual_state == "done":
        return "done"
    if manual_state == "pinned":
        return "active"
    if blockers and score >= 0.45:
        return "blocked"
    if score >= 0.72:
        return "active"
    if score >= 0.38:
        return "warming_up"
    if last_signal_at and (now - last_signal_at) > timedelta(days=DORMANT_WINDOW_DAYS):
        return "dormant"
    return "uncertain"


def _score_project(
    *,
    events: list,
    sessions: list,
    planners: list,
    reminders: list,
    repos: list,
    source_items: list,
    now: datetime,
) -> dict:
    repo_snapshots = 0
    source_dates: list[datetime] = []
    source_signal = 0.0
    for item in source_items:
        payload = item.payload or {}
        tags = payload.get("tags") or []
        metadata = payload.get("metadata") or {}
        if "repo-snapshot" in tags or metadata.get("snapshot_kind") in {"repo", "repo_signal"}:
            repo_snapshots += 1
        if not _source_item_is_meaningful(item):
            continue
        happened = _extract_iso(item.happened_at)
        if happened:
            source_dates.append(happened)
            source_signal += 0.04 * _recency_weight(happened, now=now)

    meaningful_events = [entry for entry in events if _is_meaningful_project_event(entry)]
    prioritized_events = _prioritize_project_events(meaningful_events)
    weighted_events = prioritized_events[:6]
    open_loops = [entry for entry in meaningful_events if getattr(entry, "open_question", None)]
    blocker_events = [
        entry
        for entry in meaningful_events
        if getattr(entry, "constraint", None)
        or "block" in (getattr(entry, "title", "") or "").lower()
        or "blocked" in (getattr(entry, "summary", "") or "").lower()
    ]
    due_soon = [
        reminder
        for reminder in reminders
        if reminder.next_fire_at and reminder.next_fire_at <= (_utcnow() + timedelta(days=2))
    ]
    corroborated = bool(sessions or planners or reminders or meaningful_events)
    weighted_story_signals = sum(
        _project_event_activity_weight(entry) * _recency_weight(getattr(entry, "happened_at", None), now=now)
        for entry in weighted_events
    )
    weighted_conversation_signals = sum(
        0.28 * _recency_weight(_session_signal_at(session_item), now=now)
        for session_item in sessions[:6]
    )
    weighted_planning_signals = sum(
        0.38 * _recency_weight(getattr(note, "updated_at", None), now=now)
        for note in planners[:4]
    )
    weighted_reminder_signals = sum(
        0.12 * _recency_weight(getattr(reminder, "next_fire_at", None), now=now)
        for reminder in reminders[:6]
    )
    freshness_feature = max(
        _recency_weight(_event_signal_at(prioritized_events[0]), now=now) if prioritized_events else 0.0,
        _recency_weight(_session_signal_at(sessions[0]), now=now) if sessions else 0.0,
        _recency_weight(source_dates[0], now=now) if source_dates else 0.0,
    )

    feature_scores = {
        "git": min(1.0, len(repos) * 0.08 + repo_snapshots * 0.08 + min(source_signal, 0.18)),
        "conversations": min(
            1.0,
            weighted_conversation_signals
            + sum(
                0.08 * _recency_weight(getattr(entry, "happened_at", None), now=now)
                for entry in meaningful_events
                if entry.entry_type == "conversation_session"
            ),
        ),
        "story": min(1.0, weighted_story_signals * 0.18 + len(open_loops) * 0.08),
        "planning": min(1.0, weighted_planning_signals),
        "reminders": min(1.0, weighted_reminder_signals + len(due_soon) * 0.25),
        "freshness": min(1.0, freshness_feature),
        "blockers": min(1.0, len(blocker_events) * 0.2),
    }
    if repo_snapshots and not corroborated:
        feature_scores["git"] = min(feature_scores["git"], 0.18)
        feature_scores["story"] = min(feature_scores["story"], 0.12)
    active_score = round(
        feature_scores["git"] * 0.24
        + feature_scores["conversations"] * 0.2
        + feature_scores["story"] * 0.18
        + feature_scores["planning"] * 0.12
        + feature_scores["reminders"] * 0.1
        + feature_scores["freshness"] * 0.16,
        3,
    )
    if repo_snapshots and not corroborated:
        active_score = min(active_score, 0.29)
    return {
        "feature_scores": feature_scores,
        "active_score": active_score,
        "open_loops": open_loops,
        "blocker_events": blocker_events,
        "source_dates": source_dates,
        "meaningful_events": meaningful_events,
        "repo_snapshots": repo_snapshots,
        "corroborated": corroborated,
    }


def _project_mentions_in_planner(project_title: str, planner_note) -> bool:
    metadata = planner_note.metadata_ or {}
    planner_projects = [str(item).strip().lower() for item in metadata.get("planner_projects") or []]
    title = project_title.strip().lower()
    return title in planner_projects or title in (planner_note.content or "").lower()


def _is_meaningful_project_event(entry) -> bool:
    return _project_event_activity_weight(entry) >= 0.5


def _derive_recent_state_hints(metrics: ProjectMetrics) -> dict:
    prioritized_events = _prioritize_project_events(metrics.events)
    direct_events = [entry for entry in prioritized_events if getattr(entry, "entry_type", "") in DIRECT_STATE_ENTRY_TYPES]
    newest_direct = direct_events[0] if direct_events else None
    freshest_signal = newest_direct or (prioritized_events[0] if prioritized_events else None)
    fresh_direct_signal = bool(
        newest_direct
        and _recency_weight(_event_signal_at(newest_direct), now=_utcnow()) >= 0.75
    )

    implemented = None
    if freshest_signal:
        implemented = (
            _preferred_event_summary(freshest_signal)
            or _compact_text(getattr(freshest_signal, "outcome", None))
            or _compact_text(getattr(freshest_signal, "title", None))
        )
    remaining = next(
        (
            getattr(entry, "open_question", None) or getattr(entry, "constraint", None)
            for entry in prioritized_events
            if getattr(entry, "open_question", None) or getattr(entry, "constraint", None)
        ),
        None,
    )
    if fresh_direct_signal and newest_direct:
        blockers = _dedupe_nonempty(
            [getattr(newest_direct, "constraint", None) or getattr(newest_direct, "open_question", None)]
        )
    else:
        blockers = _dedupe_nonempty(
            [getattr(entry, "constraint", None) or getattr(entry, "open_question", None) for entry in prioritized_events[:4]]
        )
    return {
        "prioritized_events": prioritized_events,
        "direct_events": direct_events,
        "fresh_direct_signal": fresh_direct_signal,
        "implemented": implemented,
        "remaining": remaining,
        "what_changed": " | ".join(
            _dedupe_nonempty([getattr(entry, "title", None) for entry in prioritized_events[:2]])
        ),
        "blockers": blockers,
    }


def _build_project_assessment_context(metrics: ProjectMetrics) -> str:
    state_hints = _derive_recent_state_hints(metrics)
    lines = [
        f"Project: {metrics.project.title}",
        f"Canonical summary: {(metrics.project.content or 'none')[:600]}",
        f"Current status guess: {metrics.status}",
        f"Activity score: {metrics.active_score}",
        f"Why active: {metrics.why_active}",
        f"Why not active: {metrics.why_not_active}",
        "",
        "Feature scores:",
    ]
    lines.extend(f"- {name}: {value}" for name, value in metrics.feature_scores.items())
    if state_hints["prioritized_events"]:
        lines.extend(["", "Newest direct state evidence (highest priority first):"])
        for entry in state_hints["prioritized_events"][:6]:
            lines.append(
                f"- {entry.happened_at.isoformat()}: {entry.title} | summary={_preferred_event_summary(entry) or 'none'} | "
                f"outcome={entry.outcome or 'none'} | open_question={entry.open_question or 'none'}"
            )
    if metrics.repos:
        lines.extend(["", "Repos:"])
        lines.extend(
            f"- {repo.repo_name} | branch={repo.branch or 'unknown'} | path={repo.local_path or 'n/a'}"
            for repo in metrics.repos[:5]
        )
    if metrics.events:
        lines.extend(["", "Recent story events:"])
        for entry in state_hints["prioritized_events"][:10]:
            lines.append(
                f"- {entry.happened_at.isoformat()}: {entry.title} | decision={entry.decision or 'none'} | "
                f"outcome={entry.outcome or 'none'} | open_question={entry.open_question or 'none'}"
            )
    if metrics.sessions:
        lines.extend(["", "Recent conversation sessions:"])
        for session in metrics.sessions[:6]:
            lines.append(
                f"- {session.agent_kind} | turns={session.turn_count} | title_hint={session.title_hint or 'none'} | "
                f"ended={session.ended_at.isoformat() if session.ended_at else 'unknown'}"
            )
    if metrics.planners:
        lines.extend(["", "Planner mentions:"])
        for note in metrics.planners[:6]:
            lines.append(f"- {note.title} | updated={note.updated_at.isoformat()}")
    if metrics.reminders:
        lines.extend(["", "Reminders:"])
        for reminder in metrics.reminders[:6]:
            lines.append(
                f"- {reminder.title} | next_fire={human_datetime_text(reminder.next_fire_at, fallback='none')}"
            )
    return "\n".join(lines).strip()


async def _compute_metrics(
    session: AsyncSession,
    *,
    project,
    snapshot,
    recent_planners: list,
    now: datetime,
) -> ProjectMetrics:
    recent_cutoff = now - timedelta(days=RECENT_WINDOW_DAYS)
    story = await store.get_project_story(session, project.id)
    recent_events = [entry for entry in (story["journal_entries"] if story else []) if entry.happened_at >= recent_cutoff]
    sessions = await store.list_conversation_sessions(session, project_note_id=project.id, since=recent_cutoff, limit=20)
    repos = story["repos"] if story else []
    source_items = [
        item
        for item in (story["source_items"] if story else [])
        if (_extract_iso(item.happened_at) or item.created_at) >= recent_cutoff
    ]
    planners = [note for note in recent_planners if _project_mentions_in_planner(project.title, note)]
    reminders = await store.list_project_reminders(session, project_note_id=project.id, status="active", limit=20)

    score_payload = _score_project(
        events=recent_events,
        sessions=sessions,
        planners=planners,
        reminders=reminders,
        repos=repos,
        source_items=source_items,
        now=now,
    )
    if is_low_signal_project_name(project.title) and not repos and not planners and not reminders:
        score_payload["active_score"] = min(score_payload["active_score"], 0.24)
        score_payload["feature_scores"]["story"] = min(score_payload["feature_scores"]["story"], 0.22)
    last_signal_candidates = [
        *(entry.happened_at for entry in score_payload["meaningful_events"]),
        *(session.ended_at for session in sessions if session.ended_at),
        *(item.updated_at for item in planners),
        *(item.next_fire_at for item in reminders if item.next_fire_at),
        *score_payload["source_dates"],
    ]
    last_signal_at = max(last_signal_candidates) if last_signal_candidates else None
    blocker_labels = []
    for entry in score_payload["blocker_events"][:5]:
        label = entry.constraint or entry.open_question or entry.title
        if label and label not in blocker_labels:
            blocker_labels.append(label)

    manual_state = getattr(snapshot, "manual_state", "normal") if snapshot else "normal"
    status = _status_from_score(
        score=score_payload["active_score"],
        manual_state=manual_state,
        blockers=blocker_labels,
        last_signal_at=last_signal_at,
        now=now,
    )
    why_active_parts = []
    if repos:
        why_active_parts.append(f"{len(repos)} linked repos")
    if sessions:
        why_active_parts.append(f"{len(sessions)} recent agent conversations")
    if score_payload["meaningful_events"]:
        why_active_parts.append(f"{len(score_payload['meaningful_events'])} high-signal story events")
    if planners:
        why_active_parts.append(f"{len(planners)} planner mentions")
    if reminders:
        why_active_parts.append(f"{len(reminders)} active reminders")
    why_not_active_parts = []
    if score_payload["repo_snapshots"] and not score_payload["corroborated"]:
        why_not_active_parts.append("recent signals are mostly collector snapshots")
    if not sessions:
        why_not_active_parts.append("little recent agent reasoning")
    if not score_payload["meaningful_events"]:
        why_not_active_parts.append("little recent high-signal project movement")
    if not planners:
        why_not_active_parts.append("not present in recent planners")
    if last_signal_at and (now - last_signal_at) > timedelta(days=7):
        why_not_active_parts.append("signals are aging")

    return ProjectMetrics(
        project=project,
        snapshot=snapshot,
        events=score_payload["meaningful_events"],
        sessions=sessions,
        repos=repos,
        source_items=source_items,
        planners=planners,
        reminders=reminders,
        feature_scores=score_payload["feature_scores"],
        active_score=score_payload["active_score"],
        status=status,
        last_signal_at=last_signal_at,
        blockers=blocker_labels,
        why_active=", ".join(why_active_parts) or "Signals are present but still thin.",
        why_not_active=", ".join(why_not_active_parts) or "No major inactivity flags surfaced.",
    )


def _fallback_project_assessment(metrics: ProjectMetrics) -> dict:
    state_hints = _derive_recent_state_hints(metrics)
    recent_titles = ", ".join(entry.title for entry in state_hints["prioritized_events"][:3]) or "Signals are still thin."
    recent_open = state_hints["remaining"]
    holes = []
    if not metrics.sessions:
        holes.append("Conversation context is still thin for this project.")
    if not metrics.repos:
        holes.append("No linked repo evidence is attached.")
    if not holes:
        holes.append("Need stronger proof of completion and deployment state.")
    return {
        "implemented": state_hints["implemented"] or recent_titles,
        "remaining": recent_open or "Need clearer evidence of what remains.",
        "blockers": state_hints["blockers"] or metrics.blockers,
        "risks": [metrics.why_not_active] if metrics.why_not_active else [],
        "holes": holes,
        "what_changed": state_hints["what_changed"] or recent_titles,
        "why_active": metrics.why_active,
        "why_not_active": metrics.why_not_active,
        "confidence": 0.55 if metrics.events else 0.35,
    }


async def recompute_project_states(
    session: AsyncSession,
    *,
    project_note_ids: list[uuid.UUID] | None = None,
    max_assessed_projects: int = MAX_ASSESSED_PROJECTS,
) -> list:
    now = _utcnow()
    projects = await store.list_project_notes(session, limit=200)
    if project_note_ids:
        wanted = {str(project_id) for project_id in project_note_ids}
        projects = [project for project in projects if str(project.id) in wanted]
    snapshot_map = {
        str(snapshot.project_note_id): snapshot
        for snapshot in await store.list_project_state_snapshots(session, limit=500)
    }
    recent_planners = await store.list_recent_planner_notes(
        session,
        since=now - timedelta(days=RECENT_WINDOW_DAYS),
        limit=100,
    )

    metrics_by_project: list[ProjectMetrics] = []
    for project in projects:
        metrics = await _compute_metrics(
            session,
            project=project,
            snapshot=snapshot_map.get(str(project.id)),
            recent_planners=recent_planners,
            now=now,
        )
        metrics_by_project.append(metrics)

    metrics_by_project.sort(
        key=lambda item: (
            1 if getattr(item.snapshot, "manual_state", "normal") == "pinned" else 0,
            0 if getattr(item.snapshot, "manual_state", "normal") == "ignored" else 1,
            item.active_score,
            item.last_signal_at or datetime(1970, 1, 1, tzinfo=timezone.utc),
        ),
        reverse=True,
    )

    assess_ids = {
        str(item.project.id)
        for item in metrics_by_project[:max_assessed_projects]
        if item.active_score >= 0.2 or getattr(item.snapshot, "manual_state", "normal") == "pinned"
    }
    if project_note_ids:
        assess_ids.update(str(project_id) for project_id in project_note_ids)

    saved_snapshots = []
    alias_map = {project.title: project for project in projects}
    connection_counts: dict[tuple[str, str], int] = defaultdict(int)
    connection_metadata: dict[tuple[str, str], dict] = {}

    for metrics in metrics_by_project:
        assessment = _fallback_project_assessment(metrics)
        state_hints = _derive_recent_state_hints(metrics)
        if str(metrics.project.id) in assess_ids:
            try:
                assessment = {
                    **assessment,
                    **(await assess_project_state(
                        session,
                        project_name=metrics.project.title,
                        context_text=_build_project_assessment_context(metrics),
                    )),
                }
            except Exception:
                pass
        if state_hints["fresh_direct_signal"]:
            if state_hints["implemented"]:
                assessment["implemented"] = state_hints["implemented"]
            if state_hints["remaining"]:
                assessment["remaining"] = state_hints["remaining"]
            if state_hints["what_changed"]:
                assessment["what_changed"] = state_hints["what_changed"]
            assessment["blockers"] = list(state_hints["blockers"] or [])
        else:
            assessment["blockers"] = _dedupe_nonempty(
                [*(state_hints["blockers"] or []), *(assessment.get("blockers") or metrics.blockers)]
            )

        confidence = float(assessment.get("confidence") or 0.0)
        snapshot = await store.upsert_project_state_snapshot(
            session,
            project_note_id=metrics.project.id,
            active_score=0.98 if getattr(metrics.snapshot, "manual_state", "normal") == "pinned" else metrics.active_score,
            status=metrics.status,
            confidence=max(confidence, 0.35 if metrics.events else 0.2),
            implemented=assessment.get("implemented"),
            remaining=assessment.get("remaining"),
            blockers=list(assessment.get("blockers") or metrics.blockers),
            risks=list(assessment.get("risks") or []),
            holes=list(assessment.get("holes") or []),
            what_changed=assessment.get("what_changed"),
            why_active=assessment.get("why_active") or metrics.why_active,
            why_not_active=assessment.get("why_not_active") or metrics.why_not_active,
            last_signal_at=metrics.last_signal_at,
            feature_scores=metrics.feature_scores,
            metadata_={
                "repo_count": len(metrics.repos),
                "session_count": len(metrics.sessions),
                "planner_mentions": len(metrics.planners),
                "reminder_count": len(metrics.reminders),
            },
        )
        saved_snapshots.append(snapshot)

        related_titles: set[str] = set()
        for note in metrics.planners:
            for candidate in (note.metadata_ or {}).get("planner_projects") or []:
                if candidate and candidate != metrics.project.title and candidate in alias_map:
                    related_titles.add(candidate)
        for entry in metrics.events:
            haystack = " ".join(
                filter(None, [entry.title, entry.summary, entry.subject_ref, entry.open_question, entry.decision])
            ).lower()
            for candidate in alias_map:
                if candidate == metrics.project.title:
                    continue
                if candidate.lower() in haystack:
                    related_titles.add(candidate)
        for related in related_titles:
            ordered = tuple(sorted([metrics.project.title, related], key=str.lower))
            connection_counts[ordered] += 1
            connection_metadata[ordered] = {
                "signals": sorted(related_titles),
                "updated_from": metrics.project.title,
            }

    await store.clear_story_connections(session, relation="co_signal")
    for (left, right), count in connection_counts.items():
        await store.upsert_story_connection(
            session,
            source_ref=left,
            target_ref=right,
            relation="co_signal",
            source_project_note_id=alias_map.get(left).id if alias_map.get(left) else None,
            target_project_note_id=alias_map.get(right).id if alias_map.get(right) else None,
            weight=min(1.0, count / 4),
            evidence_count=count,
            metadata_=connection_metadata.get((left, right)) or {},
        )

    try:
        from src.services.public_surface import refresh_public_snapshots_if_stale

        await refresh_public_snapshots_if_stale(session)
    except Exception:
        # Public snapshots are derivative; project-state refresh should still succeed if the public layer fails.
        pass

    return saved_snapshots


async def generate_case_study(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID,
    use_opus: bool = False,
    trace_id: uuid.UUID | None = None,
) -> dict | None:
    """Generate a case study for a project from brain evidence and store it in metadata."""
    from src.agents.storyteller import synthesize_project_case_study

    project = await store.get_note(session, project_note_id)
    if not project:
        return None

    snapshot = await store.get_project_state_snapshot(session, project_note_id)
    story_entries = await store.list_story_events(
        session, subject_ref=project.title, limit=20
    )

    evidence_lines = [f"Project: {project.title}"]
    if snapshot:
        evidence_lines.extend([
            f"Implemented: {snapshot.implemented or 'unknown'}",
            f"Remaining: {snapshot.remaining or 'unknown'}",
            f"Blockers: {', '.join(snapshot.blockers or []) or 'none'}",
            f"Holes: {', '.join(snapshot.holes or []) or 'none'}",
            f"What changed: {snapshot.what_changed or 'unknown'}",
        ])
    for entry in story_entries[:15]:
        summary = entry.summary or entry.body_markdown or ""
        evidence_lines.append(
            f"[{entry.entry_type}] {entry.title}: {summary}"[:300]
        )

    evidence_text = "\n".join(evidence_lines)
    case_study = await synthesize_project_case_study(
        session,
        project_name=project.title,
        evidence_text=evidence_text,
        use_opus=use_opus,
        trace_id=trace_id,
    )

    # Store in snapshot metadata
    if snapshot:
        meta = dict(snapshot.metadata_ or {})
        meta["case_study"] = case_study
        snapshot.metadata_ = meta
        await session.flush()

    return case_study


async def get_project_review_payload(
    session: AsyncSession,
    *,
    project_note_id: uuid.UUID,
) -> dict | None:
    project = await store.get_note(session, project_note_id)
    if not project:
        return None
    snapshot = await store.get_project_state_snapshot(session, project_note_id)
    if not snapshot:
        snapshots = await recompute_project_states(session, project_note_ids=[project_note_id])
        snapshot = snapshots[0] if snapshots else None
    story = await store.get_project_story(session, project_note_id)
    connections = await store.list_story_connections(session, project_note_id=project_note_id, limit=10)
    sessions = await store.list_conversation_sessions(session, project_note_id=project_note_id, limit=10)
    reminders = await store.list_project_reminders(session, project_note_id=project_note_id, status="active", limit=10)
    return {
        "project": project,
        "snapshot": snapshot,
        "story": story,
        "connections": connections,
        "sessions": sessions,
        "reminders": reminders,
    }
