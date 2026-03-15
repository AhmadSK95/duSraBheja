from __future__ import annotations

import json
from pathlib import Path

from src.collector import agent_history


def test_parse_codex_session_extracts_user_and_assistant_turns(tmp_path: Path) -> None:
    session_path = tmp_path / "codex-session.jsonl"
    rows = [
        {
            "type": "session_meta",
            "timestamp": "2026-03-12T13:00:00Z",
            "payload": {
                "id": "codex-session-1",
                "timestamp": "2026-03-12T12:55:00Z",
                "cwd": "/Users/moe/code/duSraBheja",
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-03-12T13:01:00Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Ship the story sync today"}],
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-03-12T13:02:00Z",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I'll wire the sync and deploy it."}],
            },
        },
    ]
    session_path.write_text("\n".join(json.dumps(row) for row in rows))

    parsed = agent_history.parse_codex_session(session_path)

    assert parsed is not None
    assert parsed["project_ref"] == "duSraBheja"
    assert parsed["entry_type"] == "conversation_session"
    assert parsed["metadata"]["turn_count"] == 2
    assert "Ship the story sync today" in parsed["metadata"]["redacted_transcript"]


def test_parse_claude_session_flattens_string_and_list_content(tmp_path: Path) -> None:
    session_path = tmp_path / "claude-session.jsonl"
    rows = [
        {
            "type": "user",
            "cwd": "/Users/moe/Desktop/dataGenie",
            "sessionId": "claude-session-1",
            "slug": "deep-work",
            "timestamp": "2026-03-12T14:00:00Z",
            "message": {"role": "user", "content": "What changed since yesterday?"},
        },
        {
            "type": "assistant",
            "cwd": "/Users/moe/Desktop/dataGenie",
            "sessionId": "claude-session-1",
            "slug": "deep-work",
            "timestamp": "2026-03-12T14:02:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "We changed the sync cadence."},
                    {"type": "thinking", "thinking": "ignore me"},
                ],
            },
        },
    ]
    session_path.write_text("\n".join(json.dumps(row) for row in rows))

    parsed = agent_history.parse_claude_session(session_path)

    assert parsed is not None
    assert parsed["project_ref"] == "dataGenie"
    assert parsed["metadata"]["turn_count"] == 2
    assert "thinking" not in parsed["metadata"]["redacted_transcript"]
    assert parsed["tags"][:3] == ["claude", "conversation", "agent-history"]


def test_redact_text_masks_tokens_and_keys() -> None:
    text = "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\nAuthorization: Bearer abc.def"

    redacted = agent_history.redact_text(text)

    assert "abcdefghijklmnopqrstuvwxyz123456" not in redacted
    assert "<redacted-openai-key>" in redacted
    assert "Bearer <redacted>" in redacted


def test_parse_text_snapshot_curates_reference_signal(tmp_path: Path) -> None:
    path = tmp_path / "plan.md"
    path.write_text(
        "\n".join(
            [
                "# Plan",
                "- Ship the cleaner collector pipeline",
                "- Remove dump-shaped records from the brain",
                "Notes: keep the ongoing sync curated and lightweight",
            ]
        )
    )

    parsed = agent_history.parse_text_snapshot(
        path,
        source_type="claude_history",
        entry_type="plan_snapshot",
        title_prefix="Claude plan",
    )

    assert parsed is not None
    assert parsed["entry_type"] == "agent_plan_signal"
    assert parsed["eligible_for_boards"] is False
    assert parsed["eligible_for_project_state"] is False
    assert "High-Signal Lines" in parsed["body_markdown"]
    assert "cleaner collector pipeline" in parsed["body_markdown"]


def test_parse_todo_snapshot_curates_json_into_signal_lines(tmp_path: Path) -> None:
    path = tmp_path / "todos.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {"title": "Prune low-signal dump records", "status": "open"},
                    {"task": "Backfill curated signals after cleanup", "priority": "high"},
                ]
            }
        )
    )

    parsed = agent_history.parse_todo_snapshot(path)

    assert parsed is not None
    assert parsed["entry_type"] == "agent_todo_signal"
    assert parsed["eligible_for_boards"] is False
    assert "Prune low-signal dump records" in parsed["body_markdown"]
