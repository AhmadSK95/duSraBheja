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

RECENT_WINDOW_DAYS = 14
DORMANT_WINDOW_DAYS = 30
MAX_ASSESSED_PROJECTS = 5


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
) -> dict:
    repo_snapshots = 0
    source_dates: list[datetime] = []
    for item in source_items:
        payload = item.payload or {}
        tags = payload.get("tags") or []
        metadata = payload.get("metadata") or {}
        if "repo-snapshot" in tags or metadata.get("snapshot_kind") == "repo":
            repo_snapshots += 1
        happened = _extract_iso(item.happened_at)
        if happened:
            source_dates.append(happened)

    open_loops = [entry for entry in events if getattr(entry, "open_question", None)]
    blocker_events = [
        entry
        for entry in events
        if getattr(entry, "constraint", None)
        or "block" in (getattr(entry, "title", "") or "").lower()
        or "blocked" in (getattr(entry, "summary", "") or "").lower()
    ]
    due_soon = [
        reminder
        for reminder in reminders
        if reminder.next_fire_at and reminder.next_fire_at <= (_utcnow() + timedelta(days=2))
    ]

    feature_scores = {
        "git": min(1.0, len(repos) * 0.12 + repo_snapshots * 0.22 + len(source_items[:6]) * 0.03),
        "conversations": min(1.0, len(sessions) * 0.22 + len([e for e in events if e.entry_type == "conversation_session"]) * 0.1),
        "story": min(1.0, len(events) * 0.08 + len(open_loops) * 0.08),
        "planning": min(1.0, len(planners) * 0.35),
        "reminders": min(1.0, len(reminders) * 0.18 + len(due_soon) * 0.25),
        "blockers": min(1.0, len(blocker_events) * 0.2),
    }
    active_score = round(
        feature_scores["git"] * 0.32
        + feature_scores["conversations"] * 0.24
        + feature_scores["story"] * 0.2
        + feature_scores["planning"] * 0.12
        + feature_scores["reminders"] * 0.12,
        3,
    )
    return {
        "feature_scores": feature_scores,
        "active_score": active_score,
        "open_loops": open_loops,
        "blocker_events": blocker_events,
        "source_dates": source_dates,
    }


def _project_mentions_in_planner(project_title: str, planner_note) -> bool:
    metadata = planner_note.metadata_ or {}
    planner_projects = [str(item).strip().lower() for item in metadata.get("planner_projects") or []]
    title = project_title.strip().lower()
    return title in planner_projects or title in (planner_note.content or "").lower()


def _build_project_assessment_context(metrics: ProjectMetrics) -> str:
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
    if metrics.repos:
        lines.extend(["", "Repos:"])
        lines.extend(
            f"- {repo.repo_name} | branch={repo.branch or 'unknown'} | path={repo.local_path or 'n/a'}"
            for repo in metrics.repos[:5]
        )
    if metrics.events:
        lines.extend(["", "Recent story events:"])
        for entry in metrics.events[:10]:
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
                f"- {reminder.title} | next_fire={reminder.next_fire_at.isoformat() if reminder.next_fire_at else 'none'}"
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
    events = [entry for entry in (story["journal_entries"] if story else []) if entry.happened_at >= recent_cutoff]
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
        events=events,
        sessions=sessions,
        planners=planners,
        reminders=reminders,
        repos=repos,
        source_items=source_items,
    )
    last_signal_candidates = [
        *(entry.happened_at for entry in events),
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
    if planners:
        why_active_parts.append(f"{len(planners)} planner mentions")
    if reminders:
        why_active_parts.append(f"{len(reminders)} active reminders")
    why_not_active_parts = []
    if not sessions:
        why_not_active_parts.append("little recent agent reasoning")
    if not planners:
        why_not_active_parts.append("not present in recent planners")
    if last_signal_at and (now - last_signal_at) > timedelta(days=7):
        why_not_active_parts.append("signals are aging")

    return ProjectMetrics(
        project=project,
        snapshot=snapshot,
        events=events,
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
    recent_titles = ", ".join(entry.title for entry in metrics.events[:3]) or "Signals are still thin."
    recent_open = next((entry.open_question for entry in metrics.events if entry.open_question), None)
    holes = []
    if not metrics.sessions:
        holes.append("Conversation context is still thin for this project.")
    if not metrics.repos:
        holes.append("No linked repo evidence is attached.")
    if not holes:
        holes.append("Need stronger proof of completion and deployment state.")
    return {
        "implemented": recent_titles,
        "remaining": recent_open or "Need clearer evidence of what remains.",
        "blockers": metrics.blockers,
        "risks": [metrics.why_not_active] if metrics.why_not_active else [],
        "holes": holes,
        "what_changed": recent_titles,
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

    return saved_snapshots


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
