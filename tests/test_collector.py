from __future__ import annotations

import subprocess
from pathlib import Path

from src.collector.main import build_repo_snapshot, collect_entries


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

    snapshot = build_repo_snapshot(repo)

    assert snapshot is not None
    assert snapshot["project_ref"] == "brain-repo"
    assert snapshot["repo"]["name"] == "brain-repo"
    assert "Recent Commits" in snapshot["body_markdown"]
    assert "Context File: README.md" in snapshot["body_markdown"]
    assert "Context File: CLAUDE.md" in snapshot["body_markdown"]


def test_collect_entries_skips_unchanged_projects_in_sync_mode(tmp_path: Path) -> None:
    repo = tmp_path / "brain-repo"
    repo.mkdir()

    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _run(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "README.md").write_text("hello")
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "Initial commit"], cwd=repo)

    initial_entries, state = collect_entries([repo], {}, "bootstrap")
    repeat_entries, _ = collect_entries([repo], state, "sync")

    assert len(initial_entries) == 1
    assert repeat_entries == []
