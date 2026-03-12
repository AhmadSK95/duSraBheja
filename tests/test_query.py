from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.services import query as query_service


def test_detect_query_mode_prefers_explicit_story_modes() -> None:
    assert query_service.detect_query_mode("What is the latest on dataGenie?") == "latest"
    assert query_service.detect_query_mode("timeline for duSraBheja") == "timeline"
    assert query_service.detect_query_mode("what changed since yesterday on duSraBheja") == "changed_since"
    assert query_service.detect_query_mode("show sources for dataGenie blockers") == "sources"
    assert query_service.detect_query_mode("review project duSraBheja and tell me the holes") == "project_review"


def test_parse_since_boundary_supports_yesterday_and_dates() -> None:
    now = datetime(2026, 3, 12, 15, 0, tzinfo=timezone.utc)
    yesterday = query_service.parse_since_boundary("what changed since yesterday", now)
    explicit = query_service.parse_since_boundary("what changed since 2026-03-01", now)

    assert yesterday == datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc)
    assert explicit == datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_query_brain_returns_separate_brain_and_web_sources(monkeypatch) -> None:
    async def fake_resolve_project_payload(session, question):
        return None

    async def fake_resolve_subject_ref(session, question):
        return None

    async def fake_collect_sources(session, question, *, category=None, limit=8):
        return [
            {
                "id": "brain-1",
                "title": "duSraBheja local snapshot",
                "category": "project",
                "similarity": 0.91,
                "content": "Recent project evidence",
            }
        ]

    async def fake_list_story_events(session, **kwargs):
        return []

    async def fake_get_voice_profile(session, profile_name="ahmad-default"):
        return None

    async def fake_narrate_from_context(session, *, question, context_text, use_opus, trace_id):
        assert "Recent project evidence" in context_text
        return {"text": "Brain says the project is active.", "model": "test-model", "cost_usd": 0}

    async def fake_answer_question_with_web(*, question, context_hints=None):
        return {
            "answer": "Web says there is a newer external pattern worth checking.",
            "sources": [{"title": "External guide", "url": "https://example.com", "source_hint": "current practice"}],
        }

    monkeypatch.setattr(query_service, "resolve_project_payload", fake_resolve_project_payload)
    monkeypatch.setattr(query_service, "resolve_subject_ref", fake_resolve_subject_ref)
    monkeypatch.setattr(query_service, "collect_sources", fake_collect_sources)
    monkeypatch.setattr(query_service.store, "list_story_events", fake_list_story_events)
    monkeypatch.setattr(query_service.store, "get_voice_profile", fake_get_voice_profile)
    monkeypatch.setattr(query_service, "narrate_from_context", fake_narrate_from_context)
    monkeypatch.setattr(query_service, "answer_question_with_web", fake_answer_question_with_web)

    result = await query_service.query_brain(object(), question="What is the latest on duSraBheja?")

    assert result["brain_sources"][0]["title"] == "duSraBheja local snapshot"
    assert result["web_sources"][0]["title"] == "External guide"
    assert "From your brain:" in result["answer"]
    assert "From the web:" in result["answer"]
    assert "From your brain:\nFrom your brain" not in result["answer"]
