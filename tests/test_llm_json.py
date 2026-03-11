from __future__ import annotations

import pytest

from src.agents.classifier import classify
from src.lib.llm_json import LLMJSONError, parse_json_object


def test_parse_json_object_handles_fenced_json() -> None:
    parsed = parse_json_object(
        """```json
        {"category":"daily_planner","confidence":0.9}
        ```"""
    )

    assert parsed["category"] == "daily_planner"
    assert parsed["confidence"] == 0.9


def test_parse_json_object_raises_on_missing_json() -> None:
    with pytest.raises(LLMJSONError):
        parse_json_object("not json at all")


@pytest.mark.asyncio
async def test_classifier_uses_fallback_for_empty_model_response(monkeypatch) -> None:
    async def _fake_agent_call(*args, **kwargs):
        return {
            "text": "",
            "model": "claude-haiku-4-5-20251001",
            "input_tokens": 10,
            "output_tokens": 0,
            "cost_usd": 0,
            "duration_ms": 50,
        }

    monkeypatch.setattr("src.agents.classifier.agent_call", _fake_agent_call)

    result = await classify(
        session=object(),
        text="""Thursday, Mar 5th, 2026
→ Vacuumed my room
→ Job applications
→ Therapy""",
    )

    assert result["category"] == "daily_planner"
    assert result["confidence"] >= 0.75
