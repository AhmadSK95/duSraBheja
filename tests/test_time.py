from __future__ import annotations

from datetime import datetime, timezone

from src.lib import time as time_lib


def test_format_display_datetime_uses_eastern_timezone() -> None:
    value = datetime(2026, 3, 15, 13, 30, tzinfo=timezone.utc)

    rendered = time_lib.format_display_datetime(value)

    assert rendered.endswith("EDT")
    assert rendered.startswith("2026-03-15 09:30")
