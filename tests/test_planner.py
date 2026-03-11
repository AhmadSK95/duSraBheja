from __future__ import annotations

import uuid

from src.services.planner import build_planner_payload, merge_weekly_rollup


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
