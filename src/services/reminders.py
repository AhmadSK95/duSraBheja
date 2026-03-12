"""Reminder parsing, scheduling, and persistence helpers."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.base import agent_call
from src.config import settings
from src.lib.llm_json import LLMJSONError, parse_json_object
from src.lib import store

WEEKDAY_TO_INDEX = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

REMINDER_SYSTEM_PROMPT = """You parse reminder requests into a durable schedule.

Return ONLY valid JSON with this exact shape:
{
  "title": "short reminder title",
  "body": "optional body or null",
  "recurrence_kind": "once|weekly",
  "days_of_week": ["monday", "thursday"],
  "hour": 18,
  "minute": 0,
  "timezone": "America/New_York",
  "project_ref": "project or null"
}

Rules:
- Use recurrence_kind=weekly only when the user clearly asks for repeating weekdays.
- Use 24-hour integers for hour/minute.
- If the timezone is not explicit, default to America/New_York.
- Keep the title compact.
"""

TIME_RE = re.compile(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)\b", re.IGNORECASE)
EVERY_RE = re.compile(r"\bevery\s+([a-z,\sand]+)\b", re.IGNORECASE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_weekdays(values: list[str] | None) -> list[int]:
    weekdays: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        normalized = WEEKDAY_TO_INDEX.get((value or "").strip().lower())
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        weekdays.append(normalized)
    return sorted(weekdays)


def _extract_time(text: str) -> tuple[int, int] | None:
    match = TIME_RE.search(text or "")
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = match.group("meridiem").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return hour, minute


def _fallback_parse_reminder(text: str) -> dict:
    lowered = (text or "").lower()
    title = text.strip().rstrip(".")[:120] or "Reminder"
    days: list[str] = []
    every_match = EVERY_RE.search(lowered)
    if every_match:
        for token in re.split(r"[\s,]+|and", every_match.group(1)):
            token = token.strip().lower()
            if token in WEEKDAY_TO_INDEX:
                days.append(token)
    time_parts = _extract_time(lowered) or (9, 0)
    recurrence_kind = "weekly" if days else "once"
    return {
        "title": title,
        "body": None,
        "recurrence_kind": recurrence_kind,
        "days_of_week": days,
        "hour": time_parts[0],
        "minute": time_parts[1],
        "timezone": settings.digest_timezone,
        "project_ref": None,
    }


async def parse_reminder_request(
    session: AsyncSession,
    *,
    text: str,
    trace_id: uuid.UUID | None = None,
) -> dict:
    try:
        result = await agent_call(
            session,
            agent_name="reminder_parser",
            action="parse_reminder",
            prompt=text,
            system=REMINDER_SYSTEM_PROMPT,
            model=settings.opus_model,
            max_tokens=600,
            temperature=0.0,
            trace_id=trace_id,
        )
        parsed = parse_json_object(result["text"])
    except LLMJSONError:
        parsed = _fallback_parse_reminder(text)
    except Exception:
        parsed = _fallback_parse_reminder(text)
    parsed.setdefault("timezone", settings.digest_timezone)
    parsed.setdefault("recurrence_kind", "once")
    parsed.setdefault("days_of_week", [])
    parsed.setdefault("hour", 9)
    parsed.setdefault("minute", 0)
    parsed.setdefault("title", text.strip()[:120] or "Reminder")
    return parsed


def compute_next_fire_at(
    *,
    recurrence_kind: str,
    timezone_name: str,
    hour: int,
    minute: int,
    days_of_week: list[str] | None = None,
    now: datetime | None = None,
) -> datetime:
    zone = ZoneInfo(timezone_name or settings.digest_timezone)
    current = (now or _utcnow()).astimezone(zone)
    if recurrence_kind == "weekly":
        weekday_indexes = _normalize_weekdays(days_of_week)
        if not weekday_indexes:
            weekday_indexes = [current.weekday()]
        for offset in range(8):
            candidate_day = current + timedelta(days=offset)
            if candidate_day.weekday() not in weekday_indexes:
                continue
            candidate = candidate_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= current:
                continue
            return candidate.astimezone(timezone.utc)
        candidate = (current + timedelta(days=7)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate.astimezone(timezone.utc)

    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= current:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(timezone.utc)


async def store_reminder(
    session: AsyncSession,
    *,
    raw_text: str,
    note_id,
    discord_channel_id: str | None,
    project_note_id=None,
    timezone_name: str | None = None,
    trace_id: uuid.UUID | None = None,
) -> object:
    parsed = await parse_reminder_request(session, text=raw_text, trace_id=trace_id)
    timezone_name = parsed.get("timezone") or timezone_name or settings.digest_timezone
    next_fire_at = compute_next_fire_at(
        recurrence_kind=parsed.get("recurrence_kind") or "once",
        timezone_name=timezone_name,
        hour=int(parsed.get("hour") or 9),
        minute=int(parsed.get("minute") or 0),
        days_of_week=list(parsed.get("days_of_week") or []),
    )
    recurrence_rule = {
        "days_of_week": list(parsed.get("days_of_week") or []),
        "hour": int(parsed.get("hour") or 9),
        "minute": int(parsed.get("minute") or 0),
    }
    return await store.upsert_reminder(
        session,
        title=str(parsed.get("title") or raw_text[:120]),
        body=parsed.get("body"),
        note_id=note_id,
        project_note_id=project_note_id,
        timezone_name=timezone_name,
        recurrence_kind=parsed.get("recurrence_kind") or "once",
        recurrence_rule=recurrence_rule,
        next_fire_at=next_fire_at,
        delivery_channel="discord",
        discord_channel_id=discord_channel_id,
        status="active",
        metadata_={"raw_text": raw_text, "project_ref": parsed.get("project_ref")},
    )


def advance_reminder_schedule(reminder, *, now: datetime | None = None) -> dict:
    current = now or _utcnow()
    recurrence_rule = reminder.recurrence_rule or {}
    if reminder.recurrence_kind == "weekly":
        next_fire_at = compute_next_fire_at(
            recurrence_kind="weekly",
            timezone_name=reminder.timezone,
            hour=int(recurrence_rule.get("hour") or 9),
            minute=int(recurrence_rule.get("minute") or 0),
            days_of_week=list(recurrence_rule.get("days_of_week") or []),
            now=current + timedelta(minutes=1),
        )
        return {
            "next_fire_at": next_fire_at,
            "last_fired_at": current,
            "status": "active",
        }
    return {
        "next_fire_at": None,
        "last_fired_at": current,
        "status": "done",
    }
