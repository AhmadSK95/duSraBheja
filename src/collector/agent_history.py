"""Collector for Codex and Claude conversation history."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from src.collector.main import load_state, save_state, stable_id
from src.config import settings

CODEX_SESSION_GLOBS = (
    ".codex/sessions/**/*.jsonl",
    ".codex/archived_sessions/**/*.jsonl",
)
CODEX_MEMORY_GLOBS = (
    ".codex/memories/**/*",
    ".codex/session_index.jsonl",
    ".codex/AGENTS.md",
)
CLAUDE_SESSION_GLOBS = (".claude/projects/**/*.jsonl",)
CLAUDE_MEMORY_GLOBS = (
    ".claude/projects/**/memory/MEMORY.md",
    ".claude/plans/*.md",
    ".claude/todos/*.json",
)
CLAUDE_EXCLUDED_PARTS = {
    "cache",
    "debug",
    "downloads",
    "image-cache",
    "session-env",
    "telemetry",
}
TEXT_FILE_SUFFIXES = {".json", ".jsonl", ".md", ".txt"}
MAX_REQUEST_BYTES = 1_500_000
MAX_ENTRIES_PER_REQUEST = 20
MAX_REFERENCE_SIGNAL_LINES = 8

ASSIGNMENT_SECRET_RE = re.compile(
    r"(?im)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\b\s*[:=]\s*([^\s]+)"
)
BEARER_SECRET_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._-]+")
OPENAI_KEY_RE = re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}\b")
DISCORD_TOKEN_RE = re.compile(r"\b[a-zA-Z0-9_-]{24}\.[a-zA-Z0-9_-]{6}\.[a-zA-Z0-9_-]{20,}\b")
SIGNAL_HINT_RE = re.compile(
    r"\b(todo|next|plan|ship|fix|block|blocked|question|decide|decision|need|should|must|focus|priority|launch|deploy)\b",
    re.I,
)
SIGNAL_JSON_KEYS = {
    "title",
    "task",
    "todo",
    "summary",
    "content",
    "description",
    "status",
    "note",
    "question",
    "decision",
    "next_step",
    "next",
    "priority",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def redact_text(text: str) -> str:
    redacted = ASSIGNMENT_SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text or "")
    redacted = BEARER_SECRET_RE.sub("Bearer <redacted>", redacted)
    redacted = OPENAI_KEY_RE.sub("<redacted-openai-key>", redacted)
    redacted = DISCORD_TOKEN_RE.sub("<redacted-discord-token>", redacted)
    return redacted


def _clean_signal_line(value: str) -> str:
    cleaned = " ".join((value or "").strip().split())
    cleaned = cleaned.lstrip("-*•0123456789.[]() ").strip()
    return cleaned


def _dedupe(values: list[str], *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if limit is not None and len(result) >= limit:
            break
    return result


def _extract_json_signal_lines(value: object, *, depth: int = 0) -> list[str]:
    if depth > 4:
        return []
    if isinstance(value, str):
        cleaned = _clean_signal_line(value)
        if len(cleaned) < 8:
            return []
        return [cleaned[:220]]
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            lines.extend(_extract_json_signal_lines(item, depth=depth + 1))
        return lines
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, str) and key.lower() in SIGNAL_JSON_KEYS:
                cleaned = _clean_signal_line(item)
                if cleaned:
                    lines.append(f"{key}: {cleaned[:200]}")
            else:
                lines.extend(_extract_json_signal_lines(item, depth=depth + 1))
        return lines
    return []


def _reference_signal_lines(content: str) -> list[str]:
    text = redact_text(content or "")
    candidates: list[tuple[int, str]] = []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        for line in _extract_json_signal_lines(parsed):
            score = 2 + int(bool(SIGNAL_HINT_RE.search(line)))
            candidates.append((score, line))

    for raw_line in text.splitlines():
        cleaned = _clean_signal_line(raw_line)
        if len(cleaned) < 8 or cleaned.startswith(("{", "}", "[", "]")):
            continue
        score = 0
        if raw_line.lstrip().startswith(("-", "*", "[")):
            score += 2
        if SIGNAL_HINT_RE.search(cleaned):
            score += 2
        if ":" in cleaned and len(cleaned) < 160:
            score += 1
        if len(cleaned) <= 220:
            score += 1
        candidates.append((score, cleaned[:220]))

    ranked = [line for _, line in sorted(candidates, key=lambda item: (-item[0], len(item[1]), item[1].lower()))]
    return _dedupe(ranked, limit=MAX_REFERENCE_SIGNAL_LINES)


def is_idle(path: Path) -> bool:
    try:
        modified_age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return modified_age >= settings.agent_history_idle_seconds


def should_exclude_path(path: Path) -> bool:
    if any(part in CLAUDE_EXCLUDED_PARTS for part in path.parts):
        return True
    if path.name in {"auth.json", "config.toml", "highwatermark", "lock"}:
        return True
    return False


def flatten_codex_content(payload: dict) -> str:
    blocks = payload.get("content") or []
    if isinstance(blocks, str):
        return blocks
    text_parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"input_text", "output_text"} and block.get("text"):
            text_parts.append(str(block["text"]))
    return "\n".join(part for part in text_parts if part).strip()


def flatten_claude_message_content(message: dict | None) -> str:
    if not message:
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    text_parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "thinking":
            continue
        if block_type == "text" and block.get("text"):
            text_parts.append(str(block["text"]))
            continue
        if block_type == "tool_use":
            name = block.get("name") or "tool"
            tool_input = json.dumps(block.get("input", {}), sort_keys=True)
            text_parts.append(f"[tool_use:{name}] {tool_input}")
            continue
        if block_type == "tool_result":
            nested = block.get("content")
            if isinstance(nested, str):
                text_parts.append(nested)
            elif isinstance(nested, list):
                nested_text = []
                for item in nested:
                    if isinstance(item, dict) and item.get("text"):
                        nested_text.append(str(item["text"]))
                if nested_text:
                    text_parts.append("\n".join(nested_text))
            continue
        if block.get("text"):
            text_parts.append(str(block["text"]))
    return "\n".join(part for part in text_parts if part).strip()


def select_turn_highlights(turns: list[dict], limit: int = 10) -> list[dict]:
    if len(turns) <= limit:
        return turns
    head = turns[:4]
    tail = turns[-6:]
    return [*head, {"role": "system", "timestamp": None, "text": f"... {len(turns) - 10} turns omitted ..."}, *tail]


def iso_or_none(value: str | None) -> str | None:
    if not value:
        return None
    if value.endswith("Z"):
        return value.replace("Z", "+00:00")
    return value


def infer_project_ref(cwd: str | None, path: Path) -> str | None:
    if cwd:
        cleaned = Path(cwd).name.strip()
        if cleaned:
            return cleaned

    if "projects" in path.parts:
        parent = path.parts[path.parts.index("projects") + 1]
        tokens = [token for token in parent.split("-") if token]
        if tokens:
            return tokens[-1]
    return None


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def summarize_session(
    *,
    heading: str,
    project_ref: str | None,
    cwd: str | None,
    session_id: str,
    started_at: str | None,
    ended_at: str | None,
    turns: list[dict],
) -> str:
    lines = [
        f"# {heading}",
        f"Session ID: {session_id}",
        f"Project: {project_ref or 'unknown'}",
        f"CWD: {cwd or 'unknown'}",
        f"Started: {started_at or 'unknown'}",
        f"Ended: {ended_at or 'unknown'}",
        f"Turn Count: {len(turns)}",
        "",
        "## Turn Highlights",
    ]
    for turn in select_turn_highlights(turns):
        stamp = turn.get("timestamp") or "unknown-time"
        role = turn.get("role") or "unknown"
        text = (turn.get("text") or "").strip().replace("\r", "")
        if len(text) > 700:
            text = text[:700] + "..."
        lines.append(f"- [{stamp}] {role}: {text or '[no text]'}")
    return "\n".join(lines).strip()


def parse_codex_session(path: Path) -> dict | None:
    rows = read_jsonl(path)
    if not rows:
        return None

    meta = next((row.get("payload") for row in rows if row.get("type") == "session_meta"), {}) or {}
    session_id = meta.get("id") or stable_id(str(path))
    cwd = meta.get("cwd")
    project_ref = infer_project_ref(cwd, path)

    turns: list[dict] = []
    for row in rows:
        if row.get("type") != "response_item":
            continue
        payload = row.get("payload") or {}
        role = payload.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = flatten_codex_content(payload)
        if not text:
            continue
        turns.append(
            {
                "role": role,
                "timestamp": iso_or_none(row.get("timestamp")),
                "text": redact_text(text),
            }
        )

    if not turns:
        return None

    title_hint = next((turn["text"] for turn in turns if turn["role"] == "user"), turns[0]["text"])
    title = f"Codex session: {(project_ref or Path(cwd or path.parent).name)}"
    transcript = "\n\n".join(f"{turn['role']}: {turn['text']}" for turn in turns)
    body_markdown = summarize_session(
        heading="Codex Conversation Session",
        project_ref=project_ref,
        cwd=cwd,
        session_id=session_id,
        started_at=iso_or_none(meta.get("timestamp")) or turns[0]["timestamp"],
        ended_at=turns[-1]["timestamp"],
        turns=turns,
    )

    payload = {
        "agent_kind": "codex",
        "session_id": session_id,
        "cwd": cwd,
        "source_path": str(path),
        "started_at": iso_or_none(meta.get("timestamp")) or turns[0]["timestamp"],
        "ended_at": turns[-1]["timestamp"],
        "turn_count": len(turns),
        "participants": ["user", "assistant"],
        "title_hint": title_hint[:240],
        "redacted_transcript": transcript,
        "turns": turns,
    }
    return {
        "source_type": "codex_history",
        "external_id": f"codex:session:{session_id}",
        "project_ref": project_ref,
        "title": title,
        "summary": title_hint[:240],
        "category": "project" if project_ref else "note",
        "entry_type": "conversation_session",
        "body_markdown": body_markdown,
        "tags": ["codex", "conversation", "agent-history"],
        "source_links": [],
        "metadata": payload,
        "happened_at": turns[-1]["timestamp"],
        "content_hash": _json_hash(payload),
    }


def parse_codex_memory(path: Path) -> dict | None:
    if not path.is_file():
        return None
    content = redact_text(path.read_text(encoding="utf-8", errors="replace"))
    if not content.strip():
        return None

    signal_lines = _reference_signal_lines(content)
    if not signal_lines:
        return None

    title = f"Codex reference signal: {path.name}"
    body_markdown = "\n".join(
        [
            f"# {title}",
            f"Path: {path}",
            "",
            "## High-Signal Lines",
            *[f"- {line}" for line in signal_lines],
        ]
    ).strip()
    return {
        "source_type": "codex_history",
        "external_id": f"codex:memory:{stable_id(str(path))}",
        "project_ref": infer_project_ref(None, path),
        "title": title,
        "summary": " | ".join(signal_lines[:2])[:240],
        "category": "project" if infer_project_ref(None, path) else "note",
        "entry_type": "agent_reference_signal",
        "body_markdown": body_markdown,
        "tags": ["codex", "memory", "agent-history", "curated"],
        "source_links": [],
        "capture_intent": "reference",
        "intent_confidence": 0.9,
        "validation_status": "validated",
        "quality_issues": [],
        "eligible_for_boards": False,
        "eligible_for_project_state": False,
        "metadata": {
            "agent_kind": "codex",
            "source_path": str(path),
            "redacted_content": content[:4000],
            "signal_lines": signal_lines,
            "snapshot_type": "agent_memory_snapshot",
        },
        "happened_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def parse_claude_session(path: Path) -> dict | None:
    rows = read_jsonl(path)
    if not rows:
        return None

    turns: list[dict] = []
    cwd = None
    session_id = path.stem
    started_at = None
    ended_at = None
    is_subagent = "subagents" in path.parts
    slug = None
    parent_session_id = None

    for row in rows:
        row_type = row.get("type")
        if row_type in {"user", "assistant"}:
            cwd = cwd or row.get("cwd")
            session_id = row.get("sessionId") or session_id
            slug = slug or row.get("slug")
            parent_session_id = parent_session_id or row.get("parentUuid")
            text = flatten_claude_message_content(row.get("message"))
            if not text:
                continue
            timestamp = iso_or_none(row.get("timestamp"))
            started_at = started_at or timestamp
            ended_at = timestamp or ended_at
            turns.append(
                {
                    "role": row_type,
                    "timestamp": timestamp,
                    "text": redact_text(text),
                }
            )

    if not turns:
        return None

    project_ref = infer_project_ref(cwd, path)
    title_hint = next((turn["text"] for turn in turns if turn["role"] == "user"), turns[0]["text"])
    title_prefix = "Claude subagent session" if is_subagent else "Claude session"
    title = f"{title_prefix}: {(project_ref or Path(cwd or path.parent).name)}"
    transcript = "\n\n".join(f"{turn['role']}: {turn['text']}" for turn in turns)
    payload = {
        "agent_kind": "claude_subagent" if is_subagent else "claude",
        "session_id": session_id,
        "parent_session_id": parent_session_id if is_subagent else None,
        "cwd": cwd,
        "slug": slug,
        "source_path": str(path),
        "started_at": started_at,
        "ended_at": ended_at,
        "turn_count": len(turns),
        "participants": ["user", "assistant"],
        "title_hint": title_hint[:240],
        "redacted_transcript": transcript,
        "turns": turns,
    }
    return {
        "source_type": "claude_history",
        "external_id": (
            f"claude:subagent:{session_id}:{path.stem}"
            if is_subagent
            else f"claude:session:{session_id}"
        ),
        "project_ref": project_ref,
        "title": title,
        "summary": title_hint[:240],
        "category": "project" if project_ref else "note",
        "entry_type": "conversation_session",
        "body_markdown": summarize_session(
            heading="Claude Conversation Session" if not is_subagent else "Claude Subagent Session",
            project_ref=project_ref,
            cwd=cwd,
            session_id=session_id,
            started_at=started_at,
            ended_at=ended_at,
            turns=turns,
        ),
        "tags": ["claude", "conversation", "agent-history", *([slug] if slug else [])],
        "source_links": [],
        "metadata": payload,
        "happened_at": ended_at,
        "content_hash": _json_hash(payload),
    }


def parse_text_snapshot(path: Path, *, source_type: str, entry_type: str, title_prefix: str) -> dict | None:
    if not path.is_file():
        return None
    content = redact_text(path.read_text(encoding="utf-8", errors="replace"))
    if not content.strip():
        return None

    signal_lines = _reference_signal_lines(content)
    if not signal_lines:
        return None

    project_ref = infer_project_ref(None, path)
    if entry_type == "plan_snapshot":
        signal_entry_type = "agent_plan_signal"
    else:
        signal_entry_type = "agent_reference_signal"
    title = f"{title_prefix} signal: {path.stem}"
    body_markdown = "\n".join(
        [
            f"# {title}",
            f"Path: {path}",
            "",
            "## High-Signal Lines",
            *[f"- {line}" for line in signal_lines],
        ]
    ).strip()
    return {
        "source_type": source_type,
        "external_id": f"{source_type}:{entry_type}:{stable_id(str(path))}",
        "project_ref": project_ref,
        "title": title,
        "summary": " | ".join(signal_lines[:2])[:240],
        "category": "project" if project_ref else "note",
        "entry_type": signal_entry_type,
        "body_markdown": body_markdown,
        "tags": [source_type.replace("_history", ""), signal_entry_type, "agent-history", "curated"],
        "source_links": [],
        "capture_intent": "reference",
        "intent_confidence": 0.88,
        "validation_status": "validated",
        "quality_issues": [],
        "eligible_for_boards": False,
        "eligible_for_project_state": False,
        "metadata": {
            "agent_kind": source_type.replace("_history", ""),
            "source_path": str(path),
            "redacted_content": content[:4000],
            "signal_lines": signal_lines,
            "snapshot_type": entry_type,
        },
        "happened_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def parse_todo_snapshot(path: Path) -> dict | None:
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8", errors="replace")
    content = redact_text(raw)
    if not content.strip():
        return None

    signal_lines = _reference_signal_lines(content)
    if not signal_lines:
        return None

    project_ref = infer_project_ref(None, path)
    title = f"Claude todo signal: {path.stem}"
    return {
        "source_type": "claude_history",
        "external_id": f"claude_history:todo_snapshot:{stable_id(str(path))}",
        "project_ref": project_ref,
        "title": title,
        "summary": " | ".join(signal_lines[:2])[:240],
        "category": "project" if project_ref else "note",
        "entry_type": "agent_todo_signal",
        "body_markdown": "\n".join(
            [
                f"# {title}",
                f"Path: {path}",
                "",
                "## High-Signal Lines",
                *[f"- {line}" for line in signal_lines],
            ]
        ).strip(),
        "tags": ["claude", "todo", "agent-history", "curated"],
        "source_links": [],
        "capture_intent": "reference",
        "intent_confidence": 0.9,
        "validation_status": "validated",
        "quality_issues": [],
        "eligible_for_boards": False,
        "eligible_for_project_state": False,
        "metadata": {
            "agent_kind": "claude",
            "source_path": str(path),
            "redacted_content": content[:4000],
            "signal_lines": signal_lines,
            "snapshot_type": "todo_snapshot",
        },
        "happened_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def parse_path(path: Path) -> dict | None:
    if should_exclude_path(path):
        return None
    lowered = path.as_posix()
    if ".codex/" in lowered:
        if path.suffix == ".jsonl" and ("/sessions/" in lowered or "/archived_sessions/" in lowered):
            return parse_codex_session(path)
        return parse_codex_memory(path)

    if ".claude/" in lowered:
        if path.suffix == ".jsonl" and "/projects/" in lowered:
            return parse_claude_session(path)
        if lowered.endswith("/memory/memory.md"):
            return parse_text_snapshot(
                path,
                source_type="claude_history",
                entry_type="agent_memory_snapshot",
                title_prefix="Claude project memory",
            )
        if "/plans/" in lowered and path.suffix == ".md":
            return parse_text_snapshot(
                path,
                source_type="claude_history",
                entry_type="plan_snapshot",
                title_prefix="Claude plan",
            )
        if "/todos/" in lowered and path.suffix == ".json":
            return parse_todo_snapshot(path)
    return None


def collect_candidate_paths() -> list[Path]:
    home = Path.home()
    candidates: set[Path] = set()
    for pattern in (*CODEX_SESSION_GLOBS, *CODEX_MEMORY_GLOBS, *CLAUDE_SESSION_GLOBS, *CLAUDE_MEMORY_GLOBS):
        for path in home.glob(pattern):
            if path.is_dir():
                continue
            if path.suffix.lower() not in TEXT_FILE_SUFFIXES and path.name not in {"AGENTS.md", "MEMORY.md"}:
                continue
            if should_exclude_path(path):
                continue
            candidates.add(path)
    return sorted(candidates)


def should_process_path(path: Path, state: dict, mode: str) -> bool:
    if mode == "bootstrap_agent_history":
        return True
    if not is_idle(path):
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    previous = state.get(str(path), {})
    return (
        previous.get("mtime") != stat.st_mtime
        or previous.get("size") != stat.st_size
    )


def batch_payloads(entries: list[dict], *, mode: str) -> list[dict]:
    by_source: dict[str, list[dict]] = {}
    for entry in entries:
        by_source.setdefault(entry["source_type"], []).append(entry)

    payloads: list[dict] = []
    for source_type, source_entries in sorted(by_source.items()):
        source_name = "mac-codex-history" if source_type == "codex_history" else "mac-claude-history"
        batch: list[dict] = []
        batch_bytes = 0
        for entry in source_entries:
            entry_bytes = len(json.dumps(entry).encode("utf-8"))
            if batch and (len(batch) >= MAX_ENTRIES_PER_REQUEST or batch_bytes + entry_bytes > MAX_REQUEST_BYTES):
                payloads.append(
                    {
                        "source_type": source_type,
                        "source_name": source_name,
                        "mode": mode,
                        "device_name": settings.collector_device_name,
                        "entries": batch,
                    }
                )
                batch = []
                batch_bytes = 0
            batch.append({key: value for key, value in entry.items() if key != "source_type"})
            batch_bytes += entry_bytes
        if batch:
            payloads.append(
                {
                    "source_type": source_type,
                    "source_name": source_name,
                    "mode": mode,
                    "device_name": settings.collector_device_name,
                    "entries": batch,
                }
            )
    return payloads


def prepare_payloads(mode: str) -> tuple[list[dict], dict, Path, list[dict]]:
    state_path = Path(settings.agent_history_state_path).expanduser()
    previous_state = load_state(state_path)
    entries: list[dict] = []
    next_state: dict[str, dict] = {}

    for path in collect_candidate_paths():
        try:
            stat = path.stat()
        except OSError:
            continue
        next_state[str(path)] = {"mtime": stat.st_mtime, "size": stat.st_size}
        if not should_process_path(path, previous_state, mode):
            continue
        parsed = parse_path(path)
        if parsed:
            entries.append(parsed)

    entries.sort(key=lambda item: item.get("happened_at") or "", reverse=False)
    payloads = batch_payloads(entries, mode=mode)
    counts = {"codex_history": 0, "claude_history": 0}
    for entry in entries:
        counts[entry["source_type"]] = counts.get(entry["source_type"], 0) + 1
    source_summaries = [
        {
            "source_type": "codex_history",
            "source_name": "mac-codex-history",
            "items_seen": counts.get("codex_history", 0),
        },
        {
            "source_type": "claude_history",
            "source_name": "mac-claude-history",
            "items_seen": counts.get("claude_history", 0),
        },
    ]
    return payloads, next_state, state_path, source_summaries


def main() -> None:
    parser = argparse.ArgumentParser(description="Brain agent-history collector")
    parser.add_argument("mode", choices=["bootstrap_agent_history", "sync_agent_history"])
    parser.add_argument("--prepare-dir", dest="prepare_dir")
    args = parser.parse_args()

    payloads, next_state, state_path, source_summaries = prepare_payloads(args.mode)
    if args.prepare_dir:
        prepare_dir = Path(args.prepare_dir).expanduser().resolve()
        prepare_dir.mkdir(parents=True, exist_ok=True)
        (prepare_dir / "requests.json").write_text(json.dumps(payloads, indent=2))
        (prepare_dir / "next_state.json").write_text(json.dumps(next_state, indent=2, sort_keys=True))
        (prepare_dir / "meta.json").write_text(
            json.dumps(
                {
                    "mode": args.mode,
                    "items_seen": sum(len(payload["entries"]) for payload in payloads),
                    "request_count": len(payloads),
                    "state_path": str(state_path),
                    "device_name": settings.collector_device_name,
                    "sources": source_summaries,
                },
                indent=2,
            )
        )
        print(json.dumps({"status": "prepared", "items_seen": sum(len(payload["entries"]) for payload in payloads)}))
        return

    save_state(state_path, next_state)
    print(json.dumps({"status": "prepared", "items_seen": sum(len(payload["entries"]) for payload in payloads)}))


if __name__ == "__main__":
    main()
