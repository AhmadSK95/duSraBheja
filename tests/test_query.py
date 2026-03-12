from __future__ import annotations

from datetime import datetime, timezone

from src.services import query as query_service


def test_detect_query_mode_prefers_explicit_story_modes() -> None:
    assert query_service.detect_query_mode("What is the latest on dataGenie?") == "latest"
    assert query_service.detect_query_mode("timeline for duSraBheja") == "timeline"
    assert query_service.detect_query_mode("what changed since yesterday on duSraBheja") == "changed_since"
    assert query_service.detect_query_mode("show sources for dataGenie blockers") == "sources"
    assert query_service.detect_query_mode("review project duSraBheja and tell me the holes") == "project_review"


def test_parse_since_boundary_supports_yesterday_and_dates() -> None:
    now = datetime(2026, 3, 12, 15, 0, tzinfo=timezone.utc)
    yesterday = query_service.parse_since_boundary("what changed since yesterday", now)
    explicit = query_service.parse_since_boundary("what changed since 2026-03-01", now)

    assert yesterday == datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc)
    assert explicit == datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
