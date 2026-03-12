from __future__ import annotations

import uuid
from types import SimpleNamespace

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
async def test_answer_question_uses_project_story_context_when_project_matches(monkeypatch) -> None:
    captured = {}
    project_id = uuid.uuid4()

    async def _fake_embed_text(question: str):
        return [0.0, 0.0, 0.0]

    async def _fake_vector_search(session, query_embedding, limit=20, min_similarity=0.3, category=None):
        return []

    async def _fake_find_notes_by_title(session, title: str, category: str | None = None):
        if category == "project" and "dataGenie".lower() in title.lower():
            return [SimpleNamespace(id=project_id, title="dataGenie", category="project", content="Canonical summary")]
        return []

    async def _fake_build_project_story_payload(session, note_id):
        return {
            "project": {
                "id": str(project_id),
                "title": "dataGenie",
                "category": "project",
                "content": "Shipping the project assistant workflow.",
                "status": "active",
                "tags": ["ai", "workflow"],
                "updated_at": "2026-03-12T08:00:00+00:00",
            },
            "repos": [{"name": "dataGenie", "branch": "main"}],
            "recent_activity": [
                {
                    "title": "Shipped context sync improvements",
                    "summary": "Collector snapshots and planner fixes landed.",
                    "happened_at": "2026-03-12T07:45:00+00:00",
                }
            ],
            "sources": [{"title": "GitHub repo snapshot", "summary": "Repo updated this morning."}],
            "links": [],
        }

    async def _fake_agent_call(session, **kwargs):
        captured["prompt"] = kwargs["prompt"]
        return {
            "text": "Latest on dataGenie: context sync improvements landed this morning. [1]",
            "model": "claude-sonnet-4-6",
            "cost_usd": 0,
        }

    monkeypatch.setattr(retriever, "embed_text", _fake_embed_text)
    monkeypatch.setattr(retriever, "vector_search", _fake_vector_search)
    monkeypatch.setattr(retriever, "find_notes_by_title", _fake_find_notes_by_title)
    monkeypatch.setattr(retriever, "build_project_story_payload", _fake_build_project_story_payload)
    monkeypatch.setattr(retriever, "agent_call", _fake_agent_call)

    result = await retriever.answer_question(object(), "What is the latest on dataGenie project")

    assert "Recent Project Activity" in captured["prompt"]
    assert result["confidence"] == "high"
    assert result["sources"][0]["category"] == "project_story"
    assert result["answer"].startswith("Latest on dataGenie")
