"""Shared timezone helpers for human-facing brain surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from src.config import settings


def display_timezone() -> ZoneInfo:
    return ZoneInfo(settings.digest_timezone)


def coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def ensure_utc(value: datetime | str | None) -> datetime | None:
    parsed = coerce_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def timezone_label(value: datetime | str | None = None) -> str:
    local_value = to_display_time(value)
    if local_value is None:
        return settings.digest_timezone
    return local_value.tzname() or settings.digest_timezone


def to_display_time(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    utc_value = ensure_utc(value)
    if utc_value is None:
        return None
    return utc_value.astimezone(display_timezone())


def format_display_datetime(
    value: datetime | str | None,
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


def human_datetime_text(value: datetime | str | None, *, fallback: str = "unknown") -> str:
    return format_display_datetime(value, fallback=fallback)


def format_display_date(value: date | datetime | None, *, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    if isinstance(value, datetime):
        local_value = to_display_time(value)
        if local_value is None:
            return fallback
        return local_value.strftime("%Y-%m-%d")
    return value.isoformat()


def iso_display_datetime(value: datetime | str | None) -> str | None:
    local_value = to_display_time(value)
    return local_value.isoformat() if local_value else None


def describe_event_time(value: datetime | str | None) -> dict:
    utc_value = ensure_utc(value)
    local_value = to_display_time(value)
    return {
        "event_time_utc": utc_value.isoformat() if utc_value else None,
        "event_time_local": local_value.isoformat() if local_value else None,
        "display_timezone": settings.digest_timezone,
        "timezone_label": local_value.tzname() if local_value else settings.digest_timezone,
    }


def display_timestamp_fields(
    value: datetime | str | None,
    *,
    prefix: str,
    fallback: str = "unknown",
) -> dict[str, str | None]:
    info = describe_event_time(value)
    utc_key = f"{prefix}_utc"
    local_key = f"{prefix}_local"
    label_key = f"{prefix}_display"
    return {
        utc_key: info["event_time_utc"],
        local_key: info["event_time_local"],
        label_key: format_display_datetime(value, fallback=fallback),
    }


def human_datetime_payload(
    value: datetime | str | None,
    *,
    prefix: str,
    fallback: str = "unknown",
) -> dict[str, str | None]:
    payload = display_timestamp_fields(value, prefix=prefix, fallback=fallback)
    payload[prefix] = format_display_datetime(value, fallback=fallback)
    return payload


def local_date_label(value: datetime | str | None, *, fallback: str = "unknown") -> str:
    local_value = to_display_time(value)
    if local_value is None:
        return fallback
    return local_value.strftime("%A, %b %d, %Y")


def normalize_time_fields(payload: Mapping | None, *keys: str) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for key in keys:
        if payload is None:
            value = None
        else:
            value = payload.get(key)
        result.update(display_timestamp_fields(value, prefix=key))
    result["display_timezone"] = settings.digest_timezone
    return result
