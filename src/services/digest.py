"""Daily digest assembly from stored notes and story events."""

from __future__ import annotations

from datetime import date, timedelta

try:
    from src.lib import store
except ModuleNotFoundError:  # pragma: no cover - allows unit tests without app deps
    store = None


async def build_daily_digest_payload(session, *, digest_date: date) -> dict:
    if store is None:
        raise RuntimeError("store module is unavailable")

    tasks = await store.list_notes(session, category="task", limit=10)
    projects = await store.list_notes(session, category="project", limit=10)
    recent_activity = await store.list_recent_activity(session, limit=15)
    pending_reviews = await store.get_pending_reviews(session)

    recent_cutoff = digest_date - timedelta(days=7)
    project_updates = {}
    open_loops = []
    subject_counter: dict[str, int] = {}
    for entry in recent_activity:
        if entry.happened_at.date() < recent_cutoff:
            continue
        if not entry.project_note_id:
            if getattr(entry, "open_question", None):
                open_loops.append(
                    {
                        "title": entry.title,
                        "open_question": entry.open_question,
                        "happened_at": str(entry.happened_at),
                    }
                )
        else:
            project_updates.setdefault(str(entry.project_note_id), []).append({
                "title": entry.title,
                "entry_type": entry.entry_type,
                "happened_at": str(entry.happened_at),
                "decision": getattr(entry, "decision", None),
                "impact": getattr(entry, "impact", None),
                "open_question": getattr(entry, "open_question", None),
            })
        subject_ref = getattr(entry, "subject_ref", None)
        if subject_ref:
            subject_counter[subject_ref] = subject_counter.get(subject_ref, 0) + 1

    connection_summaries = [
        {"subject_ref": subject_ref, "mentions": count}
        for subject_ref, count in sorted(subject_counter.items(), key=lambda item: item[1], reverse=True)
        if count > 1
    ]

    narration_lines = [
        f"Daily digest for {digest_date.isoformat()}.",
        "Where things stand:",
    ]
    narration_lines.extend(
        f"Project {project.title} has {len(project_updates.get(str(project.id), []))} recent updates."
        for project in projects[:5]
    )
    if recent_activity:
        narration_lines.append("Recent turning points:")
        narration_lines.extend(
            f"{entry.title}. {getattr(entry, 'summary', None) or ''}".strip()
            for entry in recent_activity[:5]
        )
    if open_loops:
        narration_lines.append("Unresolved loops:")
        narration_lines.extend(
            item["open_question"]
            for item in open_loops[:3]
            if item.get("open_question")
        )

    return {
        "digest_date": digest_date.isoformat(),
        "tasks": [
            {
                "id": str(task.id),
                "title": task.title,
                "priority": task.priority,
                "status": task.status,
            }
            for task in tasks
        ],
        "projects": [
            {
                "id": str(project.id),
                "title": project.title,
                "status": project.status,
                "updates": project_updates.get(str(project.id), [])[:5],
            }
            for project in projects
        ],
        "recent_activity": [
            {
                "id": str(entry.id),
                "title": entry.title,
                "entry_type": entry.entry_type,
                "actor_name": entry.actor_name,
                "decision": getattr(entry, "decision", None),
                "impact": getattr(entry, "impact", None),
                "open_question": getattr(entry, "open_question", None),
                "happened_at": str(entry.happened_at),
            }
            for entry in recent_activity[:10]
        ],
        "pending_reviews": [
            {
                "id": str(review.id),
                "question": review.question,
            }
            for review in pending_reviews[:10]
        ],
        "open_loops": open_loops[:10],
        "story_connections": connection_summaries[:10],
        "narration_script": " ".join(line for line in narration_lines if line),
        "writing_topics": [project.title for project in projects[:5]],
    }


async def generate_or_refresh_digest(session, *, digest_date: date) -> dict:
    if store is None:
        raise RuntimeError("store module is unavailable")

    payload = await build_daily_digest_payload(session, digest_date=digest_date)
    existing = await store.get_digest_by_date(session, digest_date)
    if existing:
        existing.payload = payload
        await session.commit()
        await session.refresh(existing)
        return payload

    await store.create_digest(session, digest_date=digest_date, payload=payload)
    return payload
