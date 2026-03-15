from __future__ import annotations

import os
import subprocess
from pathlib import Path

from src.collector import apple_notes as apple_notes_collector
from src.collector.main import (
    build_context_workspace_snapshot,
    build_repo_snapshot,
    collect_entries,
    discover_repo_roots,
    run_git,
)


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True)


def test_build_repo_snapshot_includes_git_and_context_files(tmp_path: Path) -> None:
    repo = tmp_path / "brain-repo"
    repo.mkdir()

    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test User"], cwd=repo)

    (repo / "README.md").write_text("# Brain Repo\n\nSome context")
    (repo / "CLAUDE.md").write_text("Project context lives here")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "Initial commit"], cwd=repo)

    snapshot = build_repo_snapshot(repo, max_depth=4)

    assert snapshot is not None
    assert snapshot["project_ref"] == "brain-repo"
    assert snapshot["repo"]["name"] == "brain-repo"
    assert snapshot["external_id"].startswith("collector:repo:")
    assert snapshot["entry_type"] == "repo_signal_summary"
    assert snapshot["eligible_for_boards"] is False
    assert snapshot["eligible_for_project_state"] is False
    assert "Recent Commit Signals" in snapshot["body_markdown"]
    assert "README.md:" in snapshot["body_markdown"]
    assert "CLAUDE.md:" in snapshot["body_markdown"]


def test_discover_repo_roots_finds_nested_repositories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = workspace / "project-a"
    repo.mkdir()
    nested = repo / "nested-project"
    nested.mkdir(parents=True)

    for path in (repo, nested):
        _run(["git", "init"], cwd=path)
        _run(["git", "config", "user.email", "test@example.com"], cwd=path)
        _run(["git", "config", "user.name", "Test User"], cwd=path)

    repos = discover_repo_roots([workspace], max_depth=4)

    assert repo in repos
    assert nested in repos


def test_build_context_workspace_snapshot_handles_non_repo_agent_signals(tmp_path: Path) -> None:
    workspace = tmp_path / "agent-signals"
    signal_dir = workspace / ".claude"
    signal_dir.mkdir(parents=True)
    (signal_dir / "context.md").write_text("Session notes for the agent")

    snapshot = build_context_workspace_snapshot(workspace, max_depth=4)

    assert snapshot is not None
    assert snapshot["project_ref"] == "agent-signals"
    assert snapshot["entry_type"] == "workspace_signal_summary"
    assert snapshot["eligible_for_boards"] is False
    assert "Session notes for the agent" in snapshot["body_markdown"]


def test_collect_entries_skips_unchanged_items_in_sync_mode(tmp_path: Path) -> None:
    repo = tmp_path / "brain-repo"
    repo.mkdir()

    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "README.md").write_text("hello")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "Initial commit"], cwd=repo)

    initial_entries, state = collect_entries(
        [repo],
        {},
        "bootstrap",
        scan_max_depth=4,
        inventory_recent_files_limit=10,
    )
    repeat_entries, _ = collect_entries(
        [repo],
        state,
        "sync",
        scan_max_depth=4,
        inventory_recent_files_limit=10,
    )

    assert len(initial_entries) == 2
    assert {entry["entry_type"] for entry in initial_entries} == {"repo_signal_summary", "workspace_landscape_summary"}
    assert repeat_entries == []


def test_run_git_returns_empty_string_on_timeout(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    kill_calls: list[tuple[int, int]] = []

    class _TimeoutPopen:
        pid = 12345
        returncode = None

        def __init__(self, *args, **kwargs):
            assert kwargs["start_new_session"] is True
            assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd=["git", "status"], timeout=timeout)

        def kill(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", _TimeoutPopen)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: kill_calls.append((pid, sig)))

    assert run_git(["status"], cwd=repo) == ""
    assert kill_calls == [(12345, 9)]


def test_apple_notes_export_entries_are_sensitive_and_dedupe(tmp_path: Path) -> None:
    export_root = tmp_path / "apple-notes"
    notes = [
        {
            "id": "note-1",
            "title": "Launch checklist",
            "account": "iCloud",
            "folder": "duSraBheja",
            "body": "<div>password: hunter2</div><div>Ship the reboot flow</div>",
            "created_at": "2026-03-10T10:00:00Z",
            "updated_at": "2026-03-11T11:00:00Z",
        }
    ]

    apple_notes_collector.snapshot_notes(notes, export_root)
    entries, state = apple_notes_collector.collect_exported_entries(export_root, {}, "sync")

    assert len(entries) == 1
    assert entries[0]["is_sensitive"] is True
    assert "<redacted>" in entries[0]["body_markdown"]
    assert "hunter2" in entries[0]["raw_body_markdown"]

    repeat_entries, repeat_state = apple_notes_collector.collect_exported_entries(export_root, state, "sync")

    assert repeat_entries == []
    assert repeat_state["entries"] == state["entries"]
