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
        now=now,
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
        now=now,
    )

    assert result["corroborated"] is True
    collector_only = project_state._score_project(
        events=[],
        sessions=[],
        planners=[],
        reminders=[],
        repos=[SimpleNamespace(repo_name="duSraBheja")],
        source_items=[source_item],
        now=now,
    )
    assert result["active_score"] > collector_only["active_score"]


def test_knowledge_refresh_does_not_count_as_high_signal_project_activity() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    source_item = SimpleNamespace(
        payload={"tags": ["repo-snapshot"], "metadata": {"snapshot_kind": "repo"}},
        happened_at=now,
    )
    knowledge_refresh = SimpleNamespace(
        entry_type="knowledge_refresh",
        actor_type="connector",
        happened_at=now,
        open_question="Read more about this domain",
        decision=None,
        impact="Attached web research",
        outcome=None,
        constraint=None,
        title="Knowledge Base: barbershop",
        summary="External research attached",
    )

    result = project_state._score_project(
        events=[knowledge_refresh],
        sessions=[],
        planners=[],
        reminders=[],
        repos=[SimpleNamespace(repo_name="barbershop")],
        source_items=[source_item],
        now=now,
    )

    assert result["corroborated"] is False
    assert result["meaningful_events"] == []
    assert result["active_score"] <= 0.29


def test_recent_state_hints_prefer_fresh_closeout_over_older_session() -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    older = SimpleNamespace(
        entry_type="conversation_session",
        actor_type="agent",
        happened_at=now - timedelta(days=1),
        open_question="Old question",
        decision=None,
        impact=None,
        outcome="Older architecture summary",
        constraint=None,
        title="Older session",
        summary="Older architecture summary",
    )
    closeout = SimpleNamespace(
        entry_type="session_closeout",
        actor_type="agent",
        happened_at=now,
        open_question="Verify today digest output",
        decision="Use the fresh closeout first",
        impact=None,
        outcome="Ranking and bootstrap were tightened",
        constraint=None,
        title="Codex closeout: duSraBheja",
        summary="Tightened active project ranking and bootstrap behavior.",
    )
    metrics = project_state.ProjectMetrics(
        project=SimpleNamespace(title="duSraBheja", content=None),
        snapshot=None,
        events=[older, closeout],
        sessions=[],
        repos=[],
        source_items=[],
        planners=[],
        reminders=[],
        feature_scores={},
        active_score=0.5,
        status="active",
        last_signal_at=now,
        blockers=[],
        why_active="fresh work",
        why_not_active="",
    )

    hints = project_state._derive_recent_state_hints(metrics)

    assert hints["implemented"] == "Tightened active project ranking and bootstrap behavior."
    assert hints["remaining"] == "Verify today digest output"
    assert hints["what_changed"].startswith("Codex closeout: duSraBheja")
