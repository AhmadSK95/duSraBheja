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
    for entry in recent_activity:
        if entry.happened_at.date() < recent_cutoff:
            continue
        if not entry.project_note_id:
            continue
        project_updates.setdefault(str(entry.project_note_id), []).append({
            "title": entry.title,
            "entry_type": entry.entry_type,
            "happened_at": str(entry.happened_at),
        })

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
