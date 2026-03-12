from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.services import project_state


def test_status_from_score_respects_manual_and_blocked_states() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)

    assert project_state._status_from_score(
        score=0.2,
        manual_state="pinned",
        blockers=[],
        last_signal_at=now,
        now=now,
    ) == "active"
    assert project_state._status_from_score(
        score=0.5,
        manual_state="normal",
        blockers=["Waiting on deployment"],
        last_signal_at=now,
        now=now,
    ) == "blocked"
    assert project_state._status_from_score(
        score=0.1,
        manual_state="normal",
        blockers=[],
        last_signal_at=now - timedelta(days=45),
        now=now,
    ) == "dormant"


def test_score_project_caps_collector_only_repo_activity() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    source_item = SimpleNamespace(
        payload={"tags": ["repo-snapshot"], "metadata": {"snapshot_kind": "repo"}},
        happened_at=now,
    )

    result = project_state._score_project(
        events=[],
        sessions=[],
        planners=[],
        reminders=[],
        repos=[SimpleNamespace(repo_name="hadoop-single-node-cluster")],
        source_items=[source_item],
    )

    assert result["repo_snapshots"] == 1
    assert result["corroborated"] is False
    assert result["active_score"] <= 0.29


def test_meaningful_agent_event_keeps_project_corroborated() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    source_item = SimpleNamespace(
        payload={"tags": ["repo-snapshot"], "metadata": {"snapshot_kind": "repo"}},
        happened_at=now,
    )
    event = SimpleNamespace(
        entry_type="progress_update",
        actor_type="agent",
        open_question=None,
        decision=None,
        impact=None,
        outcome=None,
        constraint=None,
        title="Shipped digest repair",
        summary="Shipped digest repair",
    )

    result = project_state._score_project(
        events=[event],
        sessions=[],
        planners=[],
        reminders=[],
        repos=[SimpleNamespace(repo_name="duSraBheja")],
        source_items=[source_item],
    )

    assert result["corroborated"] is True
    collector_only = project_state._score_project(
        events=[],
        sessions=[],
        planners=[],
        reminders=[],
        repos=[SimpleNamespace(repo_name="duSraBheja")],
        source_items=[source_item],
    )
    assert result["active_score"] > collector_only["active_score"]
