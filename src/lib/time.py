"""Shared timezone helpers for human-facing brain surfaces."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from src.config import settings


def display_timezone() -> ZoneInfo:
    return ZoneInfo(settings.digest_timezone)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_display_time(value: datetime | None) -> datetime | None:
    utc_value = ensure_utc(value)
    if utc_value is None:
        return None
    return utc_value.astimezone(display_timezone())


def format_display_datetime(
    value: datetime | None,
    *,
    fallback: str = "unknown",
    include_timezone: bool = True,
) -> str:
    local_value = to_display_time(value)
    if local_value is None:
        return fallback
    if include_timezone:
        return local_value.strftime("%Y-%m-%d %I:%M %p %Z")
    return local_value.strftime("%Y-%m-%d %I:%M %p")


def format_display_date(value: date | datetime | None, *, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        local_value = to_display_time(value)
        if local_value is None:
            return fallback
        return local_value.strftime("%Y-%m-%d")
    return value.isoformat()


def iso_display_datetime(value: datetime | None) -> str | None:
    local_value = to_display_time(value)
    return local_value.isoformat() if local_value else None


def describe_event_time(value: datetime | None) -> dict:
    utc_value = ensure_utc(value)
    local_value = to_display_time(value)
    return {
        "event_time_utc": utc_value.isoformat() if utc_value else None,
        "event_time_local": local_value.isoformat() if local_value else None,
        "display_timezone": settings.digest_timezone,
    }
