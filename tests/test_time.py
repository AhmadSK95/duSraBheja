from __future__ import annotations

from datetime import datetime, timezone

from src.lib import time as time_lib


def test_format_display_datetime_uses_eastern_timezone() -> None:
    value = datetime(2026, 3, 15, 13, 30, tzinfo=timezone.utc)

    rendered = time_lib.format_display_datetime(value)

    assert rendered.endswith("EDT")
    assert rendered.startswith("2026-03-15 09:30")


def test_format_display_datetime_parses_iso_strings_in_local_time() -> None:
    rendered = time_lib.format_display_datetime("2026-03-15T13:30:00+00:00")

    assert rendered == "2026-03-15 09:30 AM EDT"


def test_describe_event_time_includes_timezone_label() -> None:
    info = time_lib.describe_event_time("2026-03-15T13:30:00+00:00")

    assert info["display_timezone"] == "America/New_York"
    assert info["timezone_label"] == "EDT"
