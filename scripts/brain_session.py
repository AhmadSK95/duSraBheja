#!/usr/bin/env python3
"""CLI wrapper for the brain's agent session bootstrap and closeout APIs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import settings  # noqa: E402


def _base_url() -> str:
    return settings.app_base_url.rstrip("/")


def _headers() -> dict[str, str]:
    if not settings.api_token:
        raise SystemExit("API_TOKEN is required in .env to use the brain session CLI.")
    return {
        "Authorization": f"Bearer {settings.api_token}",
        "Content-Type": "application/json",
    }


def _default_session_id(agent_kind: str) -> str:
    return f"{agent_kind}-{uuid.uuid4().hex[:12]}"


def _cwd_or_none(value: str | None) -> str | None:
    return value or os.getcwd()


def _read_transcript_excerpt(path: str | None) -> str | None:
    if not path:
        return None
    text = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
    return text[:4000]


def _format_list(items: list[str], *, bullet: str = "- ") -> str:
    if not items:
        return "- none"
    return "\n".join(f"{bullet}{item}" for item in items)


def _format_bootstrap_markdown(payload: dict) -> str:
    project = payload.get("project") or {}
    reboot = payload.get("reboot_brief") or {}
    voice_profile = payload.get("voice_profile") or {}
    lines = [
        f"# Brain Reboot: {project.get('title') or payload.get('session_id')}",
        "",
        f"- Agent: {payload.get('agent_kind')}",
        f"- Session ID: {payload.get('session_id')}",
    ]
    if project.get("title"):
        lines.append(f"- Project: {project['title']}")
    if reboot.get("where_it_stands"):
        lines.extend(["", "## Where It Stands", str(reboot["where_it_stands"])])
    if reboot.get("what_changed"):
        lines.extend(["", "## What Changed", str(reboot["what_changed"])])
    if reboot.get("what_is_left"):
        lines.extend(["", "## What's Left", str(reboot["what_is_left"])])
    if reboot.get("blockers"):
        lines.extend(["", "## Blockers", _format_list([str(item) for item in reboot["blockers"][:8]])])
    if reboot.get("open_loops"):
        lines.extend(["", "## Open Loops", _format_list([str(item) for item in reboot["open_loops"][:8]])])
    if payload.get("reminders"):
        lines.extend(
            [
                "",
                "## Reminders",
                _format_list([str(item.get("title") or "") for item in payload["reminders"][:8] if item.get("title")]),
            ]
        )
    if payload.get("connections"):
        lines.extend(
            [
                "",
                "## Connections",
                _format_list(
                    [
                        str(item.get("target_ref") or item.get("source_ref") or "")
                        for item in payload["connections"][:8]
                        if item.get("target_ref") or item.get("source_ref")
                    ]
                ),
            ]
        )
    if voice_profile:
        traits = voice_profile.get("traits") or {}
        lines.extend(
            [
                "",
                "## Voice Profile",
                str(voice_profile.get("summary") or "No voice profile yet."),
                f"Tone: {', '.join(traits.get('tone') or []) or 'unknown'}",
                f"Priorities: {', '.join(traits.get('priorities') or []) or 'unknown'}",
            ]
        )
    if payload.get("brain_sources"):
        lines.extend(
            [
                "",
                "## From Your Brain",
                _format_list(
                    [
                        f"{item.get('title')} ({item.get('category')}, {float(item.get('similarity') or 0):.0%})"
                        for item in payload["brain_sources"][:6]
                    ]
                ),
            ]
        )
    if payload.get("web_sources"):
        lines.extend(
            [
                "",
                "## From The Web",
                _format_list(
                    [
                        str(item.get("title") or item.get("source_hint") or "Web result")
                        for item in payload["web_sources"][:5]
                    ]
                ),
            ]
        )
    return "\n".join(lines).strip()


def _format_closeout_markdown(payload: dict) -> str:
    project = payload.get("project") or {}
    sync = payload.get("sync") or {}
    lines = [
        "# Brain Session Closeout",
        "",
        f"- Status: {payload.get('status')}",
        f"- Items Imported: {sync.get('items_imported', 0)}",
    ]
    if project.get("title"):
        lines.append(f"- Project: {project['title']}")
    if sync.get("projects_touched"):
        lines.append(f"- Projects Touched: {', '.join(sync['projects_touched'])}")
    return "\n".join(lines)


async def _post_json(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(base_url=_base_url(), timeout=90) as client:
        response = await client.post(path, headers=_headers(), json=payload)
        response.raise_for_status()
        return response.json()


async def bootstrap_command(args: argparse.Namespace) -> int:
    payload = {
        "agent_kind": args.agent_kind,
        "session_id": args.session_id or _default_session_id(args.agent_kind),
        "cwd": _cwd_or_none(args.cwd),
        "project_hint": args.project_hint,
        "task_hint": args.task_hint,
        "include_web": args.include_web,
    }
    response = await _post_json("/api/agent/session/bootstrap", payload)
    if args.format == "json":
        print(json.dumps(response, indent=2))
    else:
        print(_format_bootstrap_markdown(response))
    return 0


async def closeout_command(args: argparse.Namespace) -> int:
    payload = {
        "agent_kind": args.agent_kind,
        "session_id": args.session_id,
        "cwd": _cwd_or_none(args.cwd),
        "project_ref": args.project_ref,
        "summary": args.summary,
        "decisions": args.decision or [],
        "changes": args.change or [],
        "open_questions": args.open_question or [],
        "source_links": args.source_link or [],
        "transcript_excerpt": _read_transcript_excerpt(args.transcript_file) or args.transcript_excerpt,
    }
    response = await _post_json("/api/agent/session/closeout", payload)
    if args.format == "json":
        print(json.dumps(response, indent=2))
    else:
        print(_format_closeout_markdown(response))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Brain session bootstrap and closeout CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap", help="Fetch the current reboot brief from the brain")
    bootstrap.add_argument("--agent-kind", default="codex")
    bootstrap.add_argument("--session-id")
    bootstrap.add_argument("--cwd")
    bootstrap.add_argument("--project-hint")
    bootstrap.add_argument("--task-hint")
    bootstrap.add_argument("--include-web", action=argparse.BooleanOptionalAction, default=True)
    bootstrap.add_argument("--format", choices=("markdown", "json"), default="markdown")
    bootstrap.set_defaults(func=bootstrap_command)

    closeout = subparsers.add_parser("closeout", help="Publish a structured session closeout to the brain")
    closeout.add_argument("--agent-kind", default="codex")
    closeout.add_argument("--session-id", required=True)
    closeout.add_argument("--cwd")
    closeout.add_argument("--project-ref")
    closeout.add_argument("--summary", required=True)
    closeout.add_argument("--decision", action="append")
    closeout.add_argument("--change", action="append")
    closeout.add_argument("--open-question", action="append")
    closeout.add_argument("--source-link", action="append")
    closeout.add_argument("--transcript-file")
    closeout.add_argument("--transcript-excerpt")
    closeout.add_argument("--format", choices=("markdown", "json"), default="markdown")
    closeout.set_defaults(func=closeout_command)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(args.func(args)))


if __name__ == "__main__":
    main()
