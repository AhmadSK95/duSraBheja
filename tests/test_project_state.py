from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
