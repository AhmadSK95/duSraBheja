"""Summary-first collector for local project and agent context."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

MAX_FILE_CHARS = 4_000
CONTEXT_FILE_PATTERNS = (
    "README*",
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/*",
    ".codex/*",
)


def parse_project_roots(raw_value: str | None) -> list[Path]:
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


def read_context_files(root: Path) -> list[dict]:
    entries = []
    for pattern in CONTEXT_FILE_PATTERNS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            entries.append({
                "path": str(path),
                "content": content[:MAX_FILE_CHARS],
            })
    return entries


def build_repo_snapshot(root: Path) -> dict | None:
    git_dir = root / ".git"
    if not git_dir.exists():
        return None

    remote_url = run_git(["remote", "get-url", "origin"], cwd=root)
    branch = run_git(["branch", "--show-current"], cwd=root)
    recent_commits = run_git(["log", "--oneline", "-n", "5"], cwd=root)
    diff_summary = run_git(["diff", "--stat", "HEAD~1..HEAD"], cwd=root)
    status = run_git(["status", "--short"], cwd=root)
    context_files = read_context_files(root)

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
                f"## Context File: {Path(context_file['path']).name}",
                context_file["content"],
            ]
        )

    body_markdown = "\n".join(body_sections).strip()
    content_hash = hashlib.sha256(body_markdown.encode("utf-8")).hexdigest()

    owner = None
    repo_name = root.name
    if remote_url.endswith(".git"):
        remote_url = remote_url[:-4]
    if "github.com" in remote_url:
        tail = remote_url.split("github.com")[-1].strip(":/")
        parts = [part for part in tail.split("/") if part]
        if len(parts) >= 2:
            owner, repo_name = parts[-2], parts[-1]

    return {
        "external_id": f"collector:{root.name}:{content_hash[:12]}",
        "project_ref": root.name,
        "title": f"{root.name} local snapshot",
        "summary": f"Local context snapshot for {root.name}",
        "category": "project",
        "entry_type": "context_dump",
        "body_markdown": body_markdown,
        "tags": ["collector", "local-context"],
        "source_links": [remote_url] if remote_url else [],
        "metadata": {
            "root": str(root),
            "branch": branch,
            "recent_commits": recent_commits,
            "working_tree": status,
            "context_file_count": len(context_files),
        },
        "repo": {
            "name": repo_name,
            "owner": owner,
            "url": remote_url or None,
            "branch": branch or None,
            "local_path": str(root),
            "is_primary": True,
        },
        "content_hash": content_hash,
    }


def collect_entries(roots: list[Path], state: dict, mode: str) -> tuple[list[dict], dict]:
    entries = []
    next_state = {"projects": {}}

    for root in roots:
        snapshot = build_repo_snapshot(root)
        if not snapshot:
            continue
        next_state["projects"][str(root)] = snapshot["content_hash"]
        if mode == "sync" and state.get("projects", {}).get(str(root)) == snapshot["content_hash"]:
            continue
        entries.append(snapshot)

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


async def run(mode: str) -> dict:
    from src.config import settings

    roots = parse_project_roots(settings.collector_project_roots)
    state_path = Path(settings.collector_state_path).expanduser()
    state = load_state(state_path)
    entries, next_state = collect_entries(roots, state, mode)
    if not entries:
        save_state(state_path, next_state)
        return {"status": "noop", "items_seen": 0}

    response = await post_entries(entries, mode=mode)
    save_state(state_path, next_state)
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description="Brain collector")
    parser.add_argument("mode", choices=["bootstrap", "sync"])
    args = parser.parse_args()

    import asyncio

    result = asyncio.run(run(args.mode))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
