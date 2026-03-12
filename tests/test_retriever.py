from __future__ import annotations

import uuid

import pytest

from src.agents import retriever


def test_build_system_prompt_includes_voice_instructions(monkeypatch) -> None:
    monkeypatch.setattr(
        retriever.settings,
        "brain_voice_instructions",
        "Write like Ahmad: direct, thoughtful, low-fluff, founder-operator energy.",
    )

    prompt = retriever.build_system_prompt()

    assert "Match Ahmad's voice and tone" in prompt
    assert "founder-operator energy" in prompt


def test_build_system_prompt_omits_voice_line_when_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(retriever.settings, "brain_voice_instructions", "")

    prompt = retriever.build_system_prompt()

    assert "Match Ahmad's voice and tone" not in prompt


def test_candidate_lookup_phrases_prioritize_project_terms() -> None:
    phrases = retriever._candidate_lookup_phrases("What is the latest on dataGenie project")

    assert phrases[0] == "What is the latest on dataGenie project"
    assert "dataGenie" in phrases


@pytest.mark.asyncio
async def test_answer_question_routes_through_query_service(monkeypatch) -> None:
    captured = {}

    async def _fake_query_brain(session, *, question: str, category=None, use_opus=False):
        captured["question"] = question
        captured["category"] = category
        captured["use_opus"] = use_opus
        return {
            "mode": "latest",
            "answer": "Latest on dataGenie: context sync improvements landed this morning. [1]",
            "sources": [{"id": str(uuid.uuid4()), "title": "dataGenie", "category": "project_story", "similarity": 0.96}],
            "confidence": "high",
            "model": "claude-sonnet-4-6",
            "cost_usd": 0,
        }

    monkeypatch.setattr(retriever, "query_brain", _fake_query_brain)

    result = await retriever.answer_question(object(), "What is the latest on dataGenie project", use_opus=True)

    assert captured == {
        "question": "What is the latest on dataGenie project",
        "category": None,
        "use_opus": True,
    }
    assert result["mode"] == "latest"
    assert result["confidence"] == "high"
    assert result["sources"][0]["category"] == "project_story"
    assert result["answer"].startswith("Latest on dataGenie")
