"""Reminder scheduling task."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import settings
from src.database import async_session
from src.lib.notifications import publish as publish_notification
from src.lib.time import human_datetime_text
from src.lib import store
from src.services.reminders import advance_reminder_schedule
from src.services.story import publish_story_entry

EVENT_REMINDER_DUE = "brain:reminder_due"


async def process_due_reminders(ctx) -> dict:
    now = datetime.now(ZoneInfo(settings.digest_timezone))
    processed = 0
    async with async_session() as session:
        reminders = await store.list_due_reminders(session, due_before=now.astimezone(ZoneInfo("UTC")), limit=50)
        for reminder in reminders:
            project_ref = None
            if reminder.project_note_id:
                project = await store.get_note(session, reminder.project_note_id)
                project_ref = project.title if project else None
            update_payload = advance_reminder_schedule(reminder, now=now.astimezone(ZoneInfo("UTC")))
            updated = await store.update_reminder(session, reminder.id, **update_payload)
            await publish_story_entry(
                session,
                actor_type="system",
                actor_name="reminder-worker",
                subject_type="project" if project_ref else "topic",
                subject_ref=project_ref or reminder.title,
                entry_type="reminder_fired",
                title=reminder.title,
                body_markdown=reminder.body or reminder.title,
                project_ref=project_ref,
                summary=f"Reminder fired for {reminder.title}",
                impact="This reminder was surfaced in Discord.",
                source="agent",
                category="reminder",
            )
            await publish_notification(
                EVENT_REMINDER_DUE,
                {
                    "reminder_id": str(reminder.id),
                    "title": reminder.title,
                    "body": reminder.body,
                    "project_ref": project_ref,
                    "discord_channel_id": reminder.discord_channel_id,
                    "next_fire_at": human_datetime_text(updated.next_fire_at if updated else None, fallback="unscheduled"),
                },
            )
            processed += 1
    return {"status": "completed", "items_imported": processed}
