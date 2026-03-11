from __future__ import annotations

import subprocess
from pathlib import Path

from src.collector.main import (
    build_context_workspace_snapshot,
    build_repo_snapshot,
    collect_entries,
    discover_repo_roots,
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
    assert "Recent Commits" in snapshot["body_markdown"]
    assert "Context File: README.md" in snapshot["body_markdown"]
    assert "Context File: CLAUDE.md" in snapshot["body_markdown"]


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
    assert snapshot["entry_type"] == "context_signal_dump"
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
    assert {entry["entry_type"] for entry in initial_entries} == {"context_dump", "directory_inventory"}
    assert repeat_entries == []
