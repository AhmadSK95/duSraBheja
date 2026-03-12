from __future__ import annotations

from datetime import datetime, timezone

from src.services import reminders as reminder_service


def test_fallback_parse_reminder_extracts_weekly_days_and_time() -> None:
    parsed = reminder_service._fallback_parse_reminder("Trash duty every Monday and Thursday at 6pm")

    assert parsed["recurrence_kind"] == "weekly"
    assert parsed["days_of_week"] == ["monday", "thursday"]
    assert parsed["hour"] == 18
    assert parsed["minute"] == 0


def test_compute_next_fire_at_for_weekly_schedule() -> None:
    next_fire = reminder_service.compute_next_fire_at(
        recurrence_kind="weekly",
        timezone_name="America/New_York",
        hour=18,
        minute=0,
        days_of_week=["monday", "thursday"],
        now=datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc),
    )

    assert next_fire.tzinfo is not None
    assert next_fire > datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
