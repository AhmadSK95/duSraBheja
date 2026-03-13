from __future__ import annotations

import uuid

from src.agents.classifier import _parse_classifier_response
from src.services.planner import (
    build_planner_payload,
    detect_planner_scope,
    merge_weekly_rollup,
    validate_planner_capture,
)


def test_build_planner_payload_extracts_dates_and_items() -> None:
    payload = build_planner_payload(
        """Thursday, Mar 5th, 2026
→ Vacuumed my room
→ 2 more job applications

Wednesday, Mar 4, 2026
→ Dishes done
→ Laundry""",
        {
            "category": "weekly_planner",
            "entities": [{"type": "project", "value": "2nd Brain"}],
            "tags": ["planning"],
            "summary": "Weekly planning snapshot",
        },
    )

    assert payload["title"] == "Week of Mar 02, 2026"
    assert payload["card"]["dates"] == [
        "Wednesday, Mar 04, 2026",
        "Thursday, Mar 05, 2026",
    ]
    assert "Vacuumed my room" in payload["card"]["top_items"]
    assert payload["metadata"]["week_start"] == "2026-03-02"


def test_merge_weekly_rollup_accumulates_daily_entries() -> None:
    planner_payload = build_planner_payload(
        """Thursday, Mar 5th, 2026
→ Vacuumed my room
→ Therapy""",
        {
            "category": "daily_planner",
            "entities": [],
            "tags": [],
            "summary": "Thursday planning",
        },
    )

    merged, changed = merge_weekly_rollup({}, planner_payload, uuid.uuid4())

    assert changed is True
    assert merged["title"] == "Week of Mar 02, 2026"
    assert "Vacuumed my room" in merged["card"]["top_items"]


def test_detect_planner_scope_prefers_daily_for_single_day_page() -> None:
    text = """Thursday
→ Job applications
→ Interview prep
→ dataGenie add databricks db
→ Connectivity guide
"""

    assert detect_planner_scope(text) == "daily_planner"


def test_parse_classifier_response_corrects_weekly_to_daily_when_scope_is_single_day() -> None:
    parsed = _parse_classifier_response(
        """{
          "category": "weekly_planner",
          "confidence": 0.85,
          "entities": [],
          "tags": ["planner"],
          "priority": "medium",
          "suggested_action": "Store this planner",
          "summary": "Weekly plan"
        }""",
        """Thursday
→ Job applications
→ Interview prep
→ dataGenie add databricks db
→ Connectivity guide
""",
    )

    assert parsed["category"] == "daily_planner"
    assert parsed["confidence"] >= 0.8


def test_validate_planner_capture_flags_weekday_date_mismatch() -> None:
    result = validate_planner_capture(
        "Friday, Mar 24, 2026\n→ Job applications\n→ Interview prep",
        category="daily_planner",
        content_type="image",
    )

    assert result["validation_status"] == "needs_review"
    assert result["eligible_for_boards"] is False
    assert any(issue["code"] == "weekday_date_mismatch" for issue in result["quality_issues"])
