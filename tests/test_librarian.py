from __future__ import annotations

import pytest

from src.agents.librarian import process_artifact


@pytest.mark.asyncio
async def test_librarian_process_artifact_serializes_entities(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def _fake_agent_call(*args, **kwargs):
        captured["prompt"] = kwargs["prompt"]
        return {
            "text": '{"action":"create","title":"Profile","content":"Saved","tags":["profile"]}',
            "model": "claude-sonnet",
            "input_tokens": 12,
            "output_tokens": 8,
            "cost_usd": 0,
            "duration_ms": 12,
        }

    monkeypatch.setattr("src.agents.librarian.agent_call", _fake_agent_call)

    result = await process_artifact(
        session=object(),
        artifact_text="Ahmad profile context",
        classification={
            "category": "note",
            "entities": [{"name": "Ahmad", "type": "person"}],
            "tags": ["profile"],
            "summary": "Personal context note",
        },
    )

    assert '"name": "Ahmad"' in captured["prompt"]
    assert result["title"] == "Profile"
    assert result["content"] == "Saved"
