from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services import query as query_service


def test_detect_query_mode_prefers_explicit_story_modes() -> None:
    assert query_service.detect_query_mode("What is the latest on dataGenie?") == "latest"
    assert query_service.detect_query_mode("What are my active projects right now?") == "active_projects"
    assert query_service.detect_query_mode("Bring me up to speed on duSraBheja", "answer") == "latest"
    assert query_service.detect_query_mode("Where did I leave off on duSraBheja?") == "latest"
    assert query_service.detect_query_mode("timeline for duSraBheja") == "timeline"
    assert query_service.detect_query_mode("what changed since yesterday on duSraBheja") == "changed_since"
    assert query_service.detect_query_mode("show sources for dataGenie blockers") == "sources"
    assert query_service.detect_query_mode("review project duSraBheja and tell me the holes") == "project_review"


def test_detect_query_intent_supports_facet_questions() -> None:
    assert (
        query_service._detect_query_intent(
            "What has been on my mind lately?",
            resolved_mode="answer",
            project_payload=None,
        )
        == "facet_thoughts"
    )
    assert (
        query_service._detect_query_intent(
            "What media themes are shaping my thinking?",
            resolved_mode="answer",
            project_payload=None,
        )
        == "facet_media"
    )


def test_build_exact_answer_prefers_recent_ip_and_username() -> None:
    answer = query_service._build_exact_answer(
        "what is my droplet account ip ??",
        [
            {
                "content": "My droplet ip is 104.131.63.231 and user account is deployer",
            }
        ],
    )

    assert "104.131.63.231" in answer
    assert "deployer" in answer


def test_matches_project_context_handles_compact_and_spaced_titles() -> None:
    assert query_service._matches_project_context("duSraBheja project sync", "duSraBheja")
    assert query_service._matches_project_context("du sra bheja project sync", "duSraBheja")
    assert not query_service._matches_project_context("barbershop project sync", "duSraBheja")


def test_project_match_strength_uses_aliases_and_repo_paths() -> None:
    payload = {
        "project": {"title": "duSraBheja"},
        "aliases": [{"alias": "brain-bot"}],
        "repos": [{"name": "duSraBheja", "local_path": "/Users/moenuddeenahmadshaik/code/duSraBheja"}],
    }

    assert query_service._project_match_strength("brain-bot retrieval fixes", payload) >= 0.9
    assert query_service._project_match_strength("/Users/moenuddeenahmadshaik/code/duSraBheja snapshot", payload) >= 0.9
    assert query_service._project_match_strength("barbershop snapshot", payload) == 0.0


def test_merge_sources_prefers_project_group_for_project_queries() -> None:
    merged = query_service._merge_sources(
        intent="project_status",
        exact_sources=[
            {
                "id": "exact-1",
                "title": "Evidence gap: duSraBheja",
                "content": "older lexical hit",
                "similarity": 0.99,
                "signal_kind": "derived_system",
                "event_time_utc": "2026-03-15T10:00:00+00:00",
                "retrieval_kind": "exact_artifact",
            }
        ],
        project_sources=[
            {
                "id": "project-1",
                "title": "duSraBheja snapshot",
                "content": "fresh project snapshot",
                "similarity": 0.8,
                "signal_kind": "derived_system",
                "event_time_utc": "2026-03-15T09:00:00+00:00",
                "retrieval_kind": "project_snapshot",
            }
        ],
        vector_sources=[],
        limit=4,
    )

    assert merged[0]["id"] == "project-1"


def test_curate_vector_sources_penalizes_legacy_workspace_for_project_queries() -> None:
    payload = {
        "project": {"title": "duSraBheja"},
        "aliases": [],
        "repos": [{"name": "duSraBheja", "local_path": "/Users/moenuddeenahmadshaik/code/duSraBheja"}],
    }
    now = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
    curated = query_service._curate_vector_sources(
        [
            {
                "id": "legacy",
                "title": "Desktop snapshot",
                "content": "/Users/moenuddeenahmadshaik/Desktop/duSraBheja snapshot",
                "similarity": 0.72,
                "signal_kind": "derived_system",
                "event_time_utc": "2026-03-10T12:00:00+00:00",
                "retrieval_kind": "vector",
                "metadata": {},
            },
            {
                "id": "current",
                "title": "Current repo snapshot",
                "content": "/Users/moenuddeenahmadshaik/code/duSraBheja local snapshot",
                "similarity": 0.68,
                "signal_kind": "direct_sync",
                "event_time_utc": "2026-03-16T10:00:00+00:00",
                "retrieval_kind": "vector",
                "metadata": {},
            },
        ],
        project_payload=payload,
        intent="project_status",
        now=now,
    )

    assert curated[0]["id"] == "current"
    assert curated[0]["similarity"] > curated[1]["similarity"]


def test_parse_since_boundary_supports_yesterday_and_dates() -> None:
    now = datetime(2026, 3, 12, 15, 0, tzinfo=timezone.utc)
    yesterday = query_service.parse_since_boundary("what changed since yesterday", now)
    explicit = query_service.parse_since_boundary("what changed since 2026-03-01", now)

    assert yesterday == datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc)
    assert explicit == datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)


def test_should_use_web_enrichment_suppresses_internal_project_queries() -> None:
    allowed = query_service.should_use_web_enrichment(
        "What is the dataGenie project status??",
        resolved_mode="latest",
        resolved_intent="project_status",
        project_payload={"project": {"title": "dataGenie"}},
        evidence_quality={"overall": 0.2},
    )
    explicit = query_service.should_use_web_enrichment(
        "What is the dataGenie project status on the web?",
        resolved_mode="latest",
        resolved_intent="project_status",
        project_payload={"project": {"title": "dataGenie"}},
        evidence_quality={"overall": 0.2},
    )

    assert allowed is False
    assert explicit is True


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

    async def fake_narrate_from_context(session, *, question, context_text, persona_context, use_opus, trace_id):
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


@pytest.mark.asyncio
async def test_resolve_project_payload_prefers_alias_inference_from_full_question(monkeypatch) -> None:
    project = {"id": "project-1", "title": "duSraBheja"}

    async def fake_infer_project_from_text(session, text):
        assert "duSraBheja" in text
        return SimpleNamespace(id="project-1")

    async def fake_build_project_story_payload(session, project_note_id):
        assert project_note_id == "project-1"
        return {"project": project}

    monkeypatch.setattr(query_service, "infer_project_from_text", fake_infer_project_from_text)
    monkeypatch.setattr(query_service, "build_project_story_payload", fake_build_project_story_payload)

    payload = await query_service.resolve_project_payload(
        object(),
        "Bring me up to speed on duSraBheja. Include the latest direction and open loops.",
    )

    assert payload["project"]["title"] == "duSraBheja"


@pytest.mark.asyncio
async def test_query_brain_active_projects_uses_snapshot_overview(monkeypatch) -> None:
    async def fake_build_active_projects_overview(session, *, limit=6):
        return [
            {
                "id": "project-1",
                "title": "duSraBheja",
                "status": "active",
                "manual_state": "normal",
                "active_score": 0.91,
                "last_signal_at": "2026-03-12T19:24:00+00:00",
                "implemented": "Fresh closeout says ranking and bootstrap were tightened",
                "remaining": "Verify the new digest output",
                "what_changed": "Codex closeout: duSraBheja | local snapshot",
                "why_active": "Fresh direct work landed today",
                "why_not_active": "none",
                "blockers": [],
                "holes": [],
                "feature_scores": {"freshness": 1.0, "planning": 0.5},
                "repo_count": 1,
                "session_count": 3,
                "planner_mentions": 1,
                "reminder_count": 0,
            }
        ]

    async def fake_get_voice_profile(session, profile_name="ahmad-default"):
        return None

    async def fake_narrate_from_context(session, *, question, context_text, persona_context, use_opus, trace_id):
        assert "Active Project Board:" in context_text
        assert "duSraBheja" in context_text
        assert "evidence_counts=repos:1, sessions:3, planners:1, reminders:0" in context_text
        return {"text": "duSraBheja is the clearest current focus.", "model": "test-model", "cost_usd": 0}

    monkeypatch.setattr(query_service, "build_active_projects_overview", fake_build_active_projects_overview)
    monkeypatch.setattr(query_service.store, "get_voice_profile", fake_get_voice_profile)
    monkeypatch.setattr(query_service, "narrate_from_context", fake_narrate_from_context)

    result = await query_service.query_brain(object(), question="What are my active projects right now?", mode="answer")

    assert result["mode"] == "active_projects"
    assert result["brain_sources"][0]["title"] == "duSraBheja"
    assert result["projects"][0]["what_changed"].startswith("Codex closeout")
    assert result["answer"] == "duSraBheja is the clearest current focus."


@pytest.mark.asyncio
async def test_query_brain_uses_brain_atlas_for_facet_questions(monkeypatch) -> None:
    class FakeSnapshot:
        def as_dict(self):
            return {
                "facets": [
                    {
                        "id": "facet:thought:1",
                        "title": "Interview preparation",
                        "summary": "Interview prep and job-search pressure have been recurring strongly.",
                        "facet_type": "thoughts",
                        "attention_score": 0.82,
                        "signal_kind": "direct_human",
                        "happened_at_utc": "2026-03-15T10:00:00+00:00",
                        "open_loops": ["Which interview track deserves the next focused block?"],
                    }
                ],
                "current_headspace": [
                    {
                        "facet_id": "facet:thought:1",
                        "title": "Interview preparation",
                        "facet_type": "thoughts",
                        "summary": "Interview prep and job-search pressure have been recurring strongly.",
                        "signal_kind": "direct_human",
                        "happened_at_local": "2026-03-15 06:00 AM EDT",
                        "path_score": 0.81,
                        "anchor_count": 2,
                        "why_now": "Recent memory paths keep landing here.",
                    }
                ],
                "memory_paths": [],
                "story_river": [],
            }

    async def fake_build_brain_atlas_snapshot(session):
        return FakeSnapshot()

    async def fake_resolve_project_payload(session, question):
        return None

    async def fake_resolve_subject_ref(session, question):
        return None

    async def fake_collect_sources(session, question, *, category=None, limit=8):
        return []

    async def fake_list_story_events(session, **kwargs):
        return [
            SimpleNamespace(
                id="evt-1",
                title="Evidence gap: dataGenie",
                summary="Noisy derived event that should not leak into facet answers.",
                entry_type="blind_spot",
                actor_type="system",
                happened_at=datetime(2026, 3, 15, 11, 0, tzinfo=timezone.utc),
            )
        ]

    async def fake_get_voice_profile(session, profile_name="ahmad-default"):
        return None

    async def fake_narrate_from_context(session, *, question, context_text, persona_context, use_opus, trace_id):
        assert "Interview prep and job-search pressure" in context_text
        assert "dataGenie" not in context_text
        assert "Current headspace" in persona_context
        return {"text": "Interview prep has been the clearest recurring thought lately.", "model": "test-model", "cost_usd": 0}

    monkeypatch.setattr(query_service, "build_brain_atlas_snapshot", fake_build_brain_atlas_snapshot)
    monkeypatch.setattr(query_service, "resolve_project_payload", fake_resolve_project_payload)
    monkeypatch.setattr(query_service, "resolve_subject_ref", fake_resolve_subject_ref)
    monkeypatch.setattr(query_service, "collect_sources", fake_collect_sources)
    monkeypatch.setattr(query_service.store, "list_story_events", fake_list_story_events)
    monkeypatch.setattr(query_service.store, "get_voice_profile", fake_get_voice_profile)
    monkeypatch.setattr(query_service, "narrate_from_context", fake_narrate_from_context)

    result = await query_service.query_brain(object(), question="What has been on my mind lately?")

    assert result["ok"] is True
    assert result["intent"] == "facet_thoughts"
    assert result["brain_sources"][0]["retrieval_kind"] == "temporal_path"
    assert "Interview prep" in result["answer"]


@pytest.mark.asyncio
async def test_query_brain_project_status_prefers_project_sources_over_lexical_noise(monkeypatch) -> None:
    async def fake_resolve_project_payload(session, question):
        return {
            "project": {
                "id": "11111111-1111-1111-1111-111111111111",
                "title": "duSraBheja",
                "status": "active",
                "content": "Board-first overhaul is live.",
            },
            "snapshot": {
                "implemented": "Board-first overhaul is live.",
                "what_changed": "Recent closeout tightened retrieval.",
                "remaining": "Verify live answers.",
                "last_signal_at": "2026-03-15T10:00:00+00:00",
                "blockers": [],
                "holes": [],
            },
            "recent_activity": [
                {
                    "id": "evt-1",
                    "title": "Closeout published",
                    "summary": "Codex published a closeout about retrieval fixes.",
                    "entry_type": "session_closeout",
                    "actor_type": "agent",
                    "happened_at": "2026-03-15T09:30:00+00:00",
                }
            ],
            "sources": [],
        }

    async def fake_resolve_subject_ref(session, question):
        return "duSraBheja"

    async def fake_list_story_events(session, **kwargs):
        return []

    async def fake_collect_sources(session, question, *, category=None, limit=8):
        return []

    async def fake_collect_exact_sources(session, question, *, intent, project_payload, now, strict_project_match=False, limit=8):
        assert strict_project_match is True
        return [
            {
                "id": "exact-1",
                "title": "Evidence gap: duSraBheja",
                "content": "Old lexical artifact mentioning duSraBheja",
                "similarity": 0.99,
                "signal_kind": "derived_system",
                "event_time_utc": "2026-03-15T10:00:00+00:00",
                "retrieval_kind": "exact_artifact",
            }
        ]

    async def fake_get_voice_profile(session, profile_name="ahmad-default"):
        return None

    async def fake_narrate_from_context(session, *, question, context_text, persona_context, use_opus, trace_id):
        assert "Where it stands: Board-first overhaul is live." in context_text
        assert context_text.index("duSraBheja snapshot") < context_text.index("Evidence gap: duSraBheja")
        return {"text": "duSraBheja is live and the next step is verifying answers.", "model": "test-model", "cost_usd": 0}

    monkeypatch.setattr(query_service, "resolve_project_payload", fake_resolve_project_payload)
    monkeypatch.setattr(query_service, "resolve_subject_ref", fake_resolve_subject_ref)
    monkeypatch.setattr(query_service.store, "list_story_events", fake_list_story_events)
    monkeypatch.setattr(query_service, "collect_sources", fake_collect_sources)
    monkeypatch.setattr(query_service, "_collect_exact_sources", fake_collect_exact_sources)
    monkeypatch.setattr(query_service.store, "get_voice_profile", fake_get_voice_profile)
    monkeypatch.setattr(query_service, "narrate_from_context", fake_narrate_from_context)

    result = await query_service.query_brain(object(), question="What is the latest on the duSraBheja project??", include_web=False)

    retrieval_kinds = [item["retrieval_kind"] for item in result["brain_sources"]]
    assert retrieval_kinds[0].startswith("project_")
    assert "project_snapshot" in retrieval_kinds
    assert retrieval_kinds.index("project_snapshot") < retrieval_kinds.index("exact_artifact")
    assert result["used_project_snapshot"] is True


@pytest.mark.asyncio
async def test_collect_facet_sources_skips_low_signal_sync_thoughts() -> None:
    snapshot = {
        "facets": [
            {
                "id": "facet:thought:noise",
                "title": "Agent todo signal",
                "summary": "Workspace summary and checklist for old sync noise.",
                "facet_type": "thoughts",
                "attention_score": 0.88,
                "signal_kind": "direct_sync",
                "happened_at_utc": "2026-03-16T12:00:00+00:00",
            },
            {
                "id": "facet:thought:real",
                "title": "Interview preparation",
                "summary": "Interview prep and job-search pressure have been recurring strongly.",
                "facet_type": "thoughts",
                "attention_score": 0.82,
                "signal_kind": "direct_human",
                "happened_at_utc": "2026-03-16T12:30:00+00:00",
            },
        ],
        "current_headspace": [
            {
                "facet_id": "facet:thought:noise",
                "title": "Agent todo signal",
                "facet_type": "thoughts",
                "path_score": 0.9,
                "anchor_count": 2,
                "why_now": "Noise.",
            },
            {
                "facet_id": "facet:thought:real",
                "title": "Interview preparation",
                "facet_type": "thoughts",
                "path_score": 0.76,
                "anchor_count": 2,
                "why_now": "Recent memory paths keep landing here.",
            },
        ],
    }

    sources = await query_service._collect_facet_sources(
        object(),
        intent="facet_thoughts",
        now=datetime(2026, 3, 16, 13, 0, tzinfo=timezone.utc),
        snapshot=snapshot,
    )

    assert [item["title"] for item in sources] == ["Interview preparation"]


def test_collect_temporal_project_sources_reads_current_headspace() -> None:
    snapshot = {
        "facets": [
            {
                "id": "facet:project:1",
                "title": "duSraBheja",
                "summary": "Temporal traversal work is live.",
                "facet_type": "projects",
                "signal_kind": "direct_agent",
                "happened_at_utc": "2026-03-16T12:00:00+00:00",
                "metadata": {"workspace_path": "/Users/moenuddeenahmadshaik/code/duSraBheja"},
            }
        ],
        "current_headspace": [
            {
                "facet_id": "facet:project:1",
                "title": "duSraBheja",
                "facet_type": "projects",
                "path_score": 0.88,
                "anchor_count": 2,
                "why_now": "Recent memory paths keep landing here.",
            }
        ],
    }
    payload = {
        "project": {"title": "duSraBheja"},
        "aliases": [],
        "repos": [{"name": "duSraBheja", "local_path": "/Users/moenuddeenahmadshaik/code/duSraBheja"}],
    }

    sources = query_service._collect_temporal_project_sources(
        snapshot,
        project_payload=payload,
        now=datetime(2026, 3, 16, 13, 0, tzinfo=timezone.utc),
    )

    assert sources
    assert sources[0]["retrieval_kind"] == "temporal_path"
    assert "Why now" in sources[0]["content"]
