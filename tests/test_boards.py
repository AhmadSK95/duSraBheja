from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone

from src.services import boards as board_service


@dataclass
class FakeArtifact:
    id: str
    summary: str
    raw_text: str


def test_previous_daily_board_window_uses_fully_closed_previous_day() -> None:
    window = board_service.previous_daily_board_window(date(2026, 3, 14))

    assert window.generated_for_date == date(2026, 3, 13)
    assert window.coverage_label == "Friday, Mar 13, 2026"


def test_previous_weekly_board_window_uses_previous_monday_to_sunday() -> None:
    window = board_service.previous_weekly_board_window(date(2026, 3, 16))

    assert window.generated_for_date == date(2026, 3, 15)
    assert window.coverage_label == "Mar 09 - Mar 15, 2026"


def test_build_board_payload_excludes_invalid_artifacts(monkeypatch) -> None:
    async def fake_list_artifacts_for_window(session, *, start, end, eligible_for_boards=None, validation_status=None, eligible_for_project_state=None, limit=250):
        base = [
            {
                "artifact": FakeArtifact("a1", "Validated task capture", "Do the thing"),
                "category": "task",
                "validation_status": "validated",
                "eligible_for_boards": True,
            },
            {
                "artifact": FakeArtifact("a2", "Bad OCR", "friday mar 24"),
                "category": "daily_planner",
                "validation_status": "needs_review",
                "eligible_for_boards": False,
            },
        ]
        if validation_status == "validated":
            return [base[0]]
        return base

    async def fake_list_story_events(session, *, since=None, until=None, limit=50, ascending=False, project_note_id=None, subject_ref=None):
        return []

    monkeypatch.setattr(board_service.store, "list_artifacts_for_window", fake_list_artifacts_for_window)
    monkeypatch.setattr(board_service.store, "list_story_events", fake_list_story_events)

    payload, source_ids, excluded_ids = asyncio.run(
        board_service.build_board_payload(
            object(),
            window=board_service.daily_board_window(date(2026, 3, 13)),
        )
    )

    assert source_ids == ["a1"]
    assert excluded_ids == ["a2"]
    assert payload["source_count"] == 1
    assert payload["excluded_count"] == 1


def test_build_board_payload_excludes_low_signal_collector_snapshots(monkeypatch) -> None:
    async def fake_list_artifacts_for_window(session, *, start, end, eligible_for_boards=None, validation_status=None, eligible_for_project_state=None, limit=250):
        base = [
            {
                "artifact": FakeArtifact("a1", "duSraBheja closeout", "Shipped the board-first brain cleanup"),
                "category": "project",
                "validation_status": "validated",
                "eligible_for_boards": True,
                "tags": ["session"],
            },
            {
                "artifact": FakeArtifact("a2", "hadoop-single-node-cluster local snapshot", "Repo inventory"),
                "category": "project",
                "validation_status": "validated",
                "eligible_for_boards": True,
                "tags": ["collector", "repo-snapshot"],
            },
        ]
        base[0]["artifact"].source = "agent"
        base[0]["artifact"].metadata_ = {}
        base[1]["artifact"].source = "collector"
        base[1]["artifact"].metadata_ = {"source_metadata": {"snapshot_kind": "repo"}, "entry_type": "context_dump"}
        return base if validation_status != "validated" else base

    async def fake_list_story_events(session, *, since=None, until=None, limit=50, ascending=False, project_note_id=None, subject_ref=None):
        return []

    monkeypatch.setattr(board_service.store, "list_artifacts_for_window", fake_list_artifacts_for_window)
    monkeypatch.setattr(board_service.store, "list_story_events", fake_list_story_events)

    payload, source_ids, excluded_ids = asyncio.run(
        board_service.build_board_payload(
            object(),
            window=board_service.daily_board_window(date(2026, 3, 13)),
        )
    )

    assert source_ids == ["a1"]
    assert "a2" in excluded_ids
    assert payload["what_mattered"] == ["duSraBheja closeout"]
