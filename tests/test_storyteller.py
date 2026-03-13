from __future__ import annotations

import asyncio

from src.agents import storyteller


def test_compose_digest_sections_repairs_invalid_json(monkeypatch) -> None:
    calls = []

    async def fake_agent_call(session, *, agent_name, action, prompt, system, model, max_tokens, temperature, trace_id=None):
        calls.append(action)
        if action == "compose_digest":
            return {"text": '{"headline": "Hello", "narrative": "missing close"'}
        return {
            "text": """{
              "headline": "Morning brief",
              "narrative": "Grounded narrative",
              "recommended_tasks": [],
              "best_ideas": [],
              "project_assessments": [],
              "writing_topics": [],
              "video_recommendations": [],
              "brain_teasers": []
            }"""
        }

    monkeypatch.setattr(storyteller, "agent_call", fake_agent_call)

    result = asyncio.run(
        storyteller.compose_digest_sections(
            object(),
            digest_date="2026-03-12",
            trigger="manual",
            context_text="duSraBheja active",
        )
    )

    assert calls == ["compose_digest", "repair_digest_json"]
    assert result["headline"] == "Morning brief"
