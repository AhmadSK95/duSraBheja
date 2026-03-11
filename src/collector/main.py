"""Summary-first collector for local project, file, and agent context."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

MAX_FILE_CHARS = 4_000
TEXT_FILE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".csv",
    ".go",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".rb",
    ".rst",
    ".sh",
    ".sql",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
ROOT_CONTEXT_FILE_NAMES = {"agents.md", "claude.md"}
SIGNAL_DIR_NAMES = {".agent", ".agents", ".claude", ".codex", ".cursor", ".windsurf"}
IGNORED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


def parse_paths(raw_value: str | None) -> list[Path]:
    if not raw_value:
        return []
    return [Path(item).expanduser().resolve() for item in raw_value.split(",") if item.strip()]


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def iter_tree(root: Path, max_depth: int):
    if not root.exists():
        return

    root_depth = len(root.parts)
    for current_dir, dirnames, filenames in os.walk(root):
        current_path = Path(current_dir)
        dirnames[:] = [name for name in dirnames if name not in IGNORED_DIR_NAMES]

        depth = len(current_path.parts) - root_depth
        if depth >= max_depth:
            dirnames[:] = []

        yield current_path, [current_path / name for name in filenames]


def is_text_like(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.suffix.lower() in TEXT_FILE_SUFFIXES
        or name in ROOT_CONTEXT_FILE_NAMES
        or name.startswith("readme")
    )


def read_text_excerpt(path: Path) -> str:
    if not is_text_like(path):
        return "[binary or unsupported file]"

    try:
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
    except OSError:
        return "[unreadable file]"


def discover_repo_roots(search_roots: list[Path], max_depth: int) -> list[Path]:
    repos: set[Path] = set()
    for root in search_roots:
        for current_path, _ in iter_tree(root, max_depth) or []:
            if (current_path / ".git").exists():
                repos.add(current_path)
    return sorted(repos)


def discover_context_workspaces(search_roots: list[Path], repo_roots: list[Path], max_depth: int) -> list[Path]:
    workspaces: set[Path] = set()
    for root in search_roots:
        for current_path, files in iter_tree(root, max_depth) or []:
            workspace_root = None
            if current_path.name in SIGNAL_DIR_NAMES:
                workspace_root = current_path.parent
            elif any(file_path.name.lower() in ROOT_CONTEXT_FILE_NAMES for file_path in files):
                workspace_root = current_path

            if not workspace_root:
                continue
            if any(is_within(workspace_root, repo_root) for repo_root in repo_roots):
                continue
            workspaces.add(workspace_root)
    return sorted(workspaces)


def collect_context_files(root: Path, max_depth: int) -> list[dict]:
    entries = []
    for current_path, files in iter_tree(root, max_depth) or []:
        for path in sorted(files):
            relative_parts = path.relative_to(root).parts
            in_signal_dir = any(part in SIGNAL_DIR_NAMES for part in relative_parts)
            if not in_signal_dir and not is_text_like(path):
                continue
            if not in_signal_dir and path.name.lower() not in ROOT_CONTEXT_FILE_NAMES and not path.name.lower().startswith("readme"):
                continue

            entries.append({
                "path": str(path),
                "relative_path": str(path.relative_to(root)),
                "content": read_text_excerpt(path),
            })
    return entries


def build_repo_snapshot(root: Path, *, max_depth: int) -> dict | None:
    if not (root / ".git").exists():
        return None

    remote_url = run_git(["remote", "get-url", "origin"], cwd=root)
    branch = run_git(["branch", "--show-current"], cwd=root)
    recent_commits = run_git(["log", "--oneline", "-n", "5"], cwd=root)
    diff_summary = run_git(["diff", "--stat", "HEAD~1..HEAD"], cwd=root)
    status = run_git(["status", "--short"], cwd=root)
    context_files = collect_context_files(root, max_depth=max_depth)

    body_sections = [
        f"# Project Snapshot: {root.name}",
        f"Path: {root}",
        f"Branch: {branch or 'unknown'}",
        "",
        "## Recent Commits",
        recent_commits or "No recent commits",
        "",
        "## Diff Summary",
        diff_summary or "No diff summary",
        "",
        "## Working Tree",
        status or "Clean working tree",
    ]

    for context_file in context_files:
        body_sections.extend(
            [
                "",
                f"## Context File: {context_file['relative_path']}",
                context_file["content"],
            ]
        )

    body_markdown = "\n".join(body_sections).strip()
    content_hash = hashlib.sha256(body_markdown.encode("utf-8")).hexdigest()

    owner = None
    repo_name = root.name
    normalized_remote = remote_url[:-4] if remote_url.endswith(".git") else remote_url
    if "github.com" in normalized_remote:
        tail = normalized_remote.split("github.com")[-1].strip(":/")
        parts = [part for part in tail.split("/") if part]
        if len(parts) >= 2:
            owner, repo_name = parts[-2], parts[-1]

    return {
        "_state_key": f"repo:{root}",
        "external_id": f"collector:repo:{stable_id(str(root))}",
        "project_ref": root.name,
        "title": f"{root.name} local snapshot",
        "summary": f"Local repo and context snapshot for {root.name}",
        "category": "project",
        "entry_type": "context_dump",
        "body_markdown": body_markdown,
        "tags": ["collector", "local-context", "repo-snapshot"],
        "source_links": [normalized_remote] if normalized_remote else [],
        "metadata": {
            "root": str(root),
            "branch": branch,
            "recent_commits": recent_commits,
            "working_tree": status,
            "context_file_count": len(context_files),
            "snapshot_kind": "repo",
        },
        "repo": {
            "name": repo_name,
            "owner": owner,
            "url": normalized_remote or None,
            "branch": branch or None,
            "local_path": str(root),
            "is_primary": True,
        },
        "content_hash": content_hash,
    }


def build_context_workspace_snapshot(workspace_root: Path, *, max_depth: int) -> dict | None:
    context_files = collect_context_files(workspace_root, max_depth=max_depth)
    if not context_files:
        return None

    body_sections = [
        f"# Context Workspace Snapshot: {workspace_root.name}",
        f"Path: {workspace_root}",
        "",
        "## Context Files",
    ]
    for context_file in context_files:
        body_sections.extend(
            [
                "",
                f"### {context_file['relative_path']}",
                context_file["content"],
            ]
        )

    body_markdown = "\n".join(body_sections).strip()
    content_hash = hashlib.sha256(body_markdown.encode("utf-8")).hexdigest()

    return {
        "_state_key": f"context:{workspace_root}",
        "external_id": f"collector:context:{stable_id(str(workspace_root))}",
        "project_ref": workspace_root.name,
        "title": f"{workspace_root.name} agent context snapshot",
        "summary": f"Agent context signal snapshot for {workspace_root.name}",
        "category": "project",
        "entry_type": "context_signal_dump",
        "body_markdown": body_markdown,
        "tags": ["collector", "agent-context"],
        "source_links": [],
        "metadata": {
            "root": str(workspace_root),
            "context_file_count": len(context_files),
            "snapshot_kind": "context_workspace",
        },
        "content_hash": content_hash,
    }


def build_directory_inventory_snapshot(
    root: Path,
    *,
    repo_roots: list[Path],
    context_workspaces: list[Path],
    max_depth: int,
    recent_files_limit: int,
) -> dict | None:
    if not root.exists():
        return None

    file_counts: dict[str, int] = {}
    recent_files: list[tuple[float, Path]] = []
    total_files = 0

    for _, files in iter_tree(root, max_depth) or []:
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            suffix = path.suffix.lower() or "[no_ext]"
            file_counts[suffix] = file_counts.get(suffix, 0) + 1
            total_files += 1
            recent_files.append((stat.st_mtime, path))

    top_types = sorted(file_counts.items(), key=lambda item: item[1], reverse=True)[:10]
    recent_files.sort(reverse=True)
    recent_lines = []
    for modified_at, path in recent_files[:recent_files_limit]:
        stamp = datetime.fromtimestamp(modified_at, tz=timezone.utc).isoformat()
        recent_lines.append(f"- {stamp} {path}")

    repo_lines = [
        f"- {repo.name}: {repo}"
        for repo in repo_roots
        if is_within(repo, root)
    ] or ["- None discovered"]

    context_lines = [
        f"- {workspace.name}: {workspace}"
        for workspace in context_workspaces
        if is_within(workspace, root)
    ] or ["- None discovered"]

    type_lines = [f"- {suffix}: {count}" for suffix, count in top_types] or ["- No files found"]

    body_markdown = "\n".join(
        [
            f"# Directory Inventory: {root.name}",
            f"Path: {root}",
            f"Total Files Seen: {total_files}",
            "",
            "## Repositories",
            *repo_lines,
            "",
            "## Context Workspaces",
            *context_lines,
            "",
            "## Top File Types",
            *type_lines,
            "",
            "## Recent Files",
            *(recent_lines or ["- No files found"]),
        ]
    ).strip()

    content_hash = hashlib.sha256(body_markdown.encode("utf-8")).hexdigest()
    return {
        "_state_key": f"inventory:{root}",
        "external_id": f"collector:inventory:{stable_id(str(root))}",
        "title": f"{root.name} directory inventory",
        "summary": f"Bootstrap inventory for {root.name} with {total_files} files",
        "category": "resource",
        "entry_type": "directory_inventory",
        "body_markdown": body_markdown,
        "tags": ["collector", "inventory", root.name.lower()],
        "source_links": [],
        "metadata": {
            "root": str(root),
            "total_files": total_files,
            "repo_count": len([repo for repo in repo_roots if is_within(repo, root)]),
            "context_workspace_count": len([workspace for workspace in context_workspaces if is_within(workspace, root)]),
            "snapshot_kind": "directory_inventory",
        },
        "content_hash": content_hash,
    }


def serialize_entry(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if not key.startswith("_")}


def select_collection_roots(mode: str) -> list[Path]:
    from src.config import settings

    if mode == "bootstrap":
        raw_value = settings.collector_bootstrap_roots or settings.collector_project_roots
    else:
        raw_value = settings.collector_daily_roots or settings.collector_project_roots
    return parse_paths(raw_value)


def collect_entries(
    roots: list[Path],
    state: dict,
    mode: str,
    *,
    scan_max_depth: int,
    inventory_recent_files_limit: int,
) -> tuple[list[dict], dict]:
    repo_roots = discover_repo_roots(roots, scan_max_depth)
    context_workspaces = discover_context_workspaces(roots, repo_roots, scan_max_depth)

    candidate_entries = []
    for repo_root in repo_roots:
        snapshot = build_repo_snapshot(repo_root, max_depth=scan_max_depth)
        if snapshot:
            candidate_entries.append(snapshot)

    for workspace_root in context_workspaces:
        snapshot = build_context_workspace_snapshot(workspace_root, max_depth=scan_max_depth)
        if snapshot:
            candidate_entries.append(snapshot)

    if mode == "bootstrap":
        for root in roots:
            inventory = build_directory_inventory_snapshot(
                root,
                repo_roots=repo_roots,
                context_workspaces=context_workspaces,
                max_depth=scan_max_depth,
                recent_files_limit=inventory_recent_files_limit,
            )
            if inventory:
                candidate_entries.append(inventory)

    previous_entries = state.get("entries", {})
    next_state = {"entries": {}}
    entries = []

    for entry in candidate_entries:
        state_key = entry["_state_key"]
        content_hash = entry["content_hash"]
        next_state["entries"][state_key] = content_hash
        if mode == "sync" and previous_entries.get(state_key) == content_hash:
            continue
        entries.append(serialize_entry(entry))

    return entries, next_state


async def post_entries(entries: list[dict], *, mode: str) -> dict:
    import httpx

    from src.config import settings

    headers = {
        "Authorization": f"Bearer {settings.api_token}",
    }
    payload = {
        "source_name": "mac-collector",
        "mode": mode,
        "device_name": settings.collector_device_name,
        "entries": entries,
    }
    async with httpx.AsyncClient(base_url=settings.collector_api_base_url, timeout=60) as client:
        response = await client.post("/api/ingest/collector", headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def prepare_payload_bundle(mode: str) -> tuple[dict, dict, Path]:
    from src.config import settings

    roots = select_collection_roots(mode)
    state_path = Path(settings.collector_state_path).expanduser()
    state = load_state(state_path)
    entries, next_state = collect_entries(
        roots,
        state,
        mode,
        scan_max_depth=settings.collector_scan_max_depth,
        inventory_recent_files_limit=settings.collector_inventory_recent_files_limit,
    )
    payload = {
        "source_name": "mac-collector",
        "mode": mode,
        "device_name": settings.collector_device_name,
        "entries": entries,
    }
    return payload, next_state, state_path


async def run(mode: str) -> dict:
    payload, next_state, state_path = prepare_payload_bundle(mode)
    if not payload["entries"]:
        save_state(state_path, next_state)
        return {"status": "noop", "items_seen": 0}

    response = await post_entries(payload["entries"], mode=mode)
    save_state(state_path, next_state)
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Brain collector")
    parser.add_argument("mode", choices=["bootstrap", "sync"])
    parser.add_argument("--prepare-dir", dest="prepare_dir")
    args = parser.parse_args()

    import asyncio

    if args.prepare_dir:
        payload, next_state, state_path = prepare_payload_bundle(args.mode)
        prepare_dir = Path(args.prepare_dir).expanduser().resolve()
        prepare_dir.mkdir(parents=True, exist_ok=True)
        (prepare_dir / "payload.json").write_text(json.dumps(payload, indent=2))
        (prepare_dir / "next_state.json").write_text(json.dumps(next_state, indent=2, sort_keys=True))
        (prepare_dir / "meta.json").write_text(
            json.dumps(
                {
                    "mode": args.mode,
                    "items_seen": len(payload["entries"]),
                    "state_path": str(state_path),
                },
                indent=2,
            )
        )
        print(json.dumps({"status": "prepared", "items_seen": len(payload["entries"])}))
        return

    result = asyncio.run(run(args.mode))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
