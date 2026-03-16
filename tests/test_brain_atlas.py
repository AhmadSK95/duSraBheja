from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.services import brain_atlas


def test_interest_and_media_facets_are_derived_from_chrome_payloads() -> None:
    source_item = SimpleNamespace(
        title="Chrome weekly summary",
        summary="Searches and videos clustered around barbershop growth and interview prep.",
        happened_at=datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
        payload={
            "metadata": {
                "keyword_themes": [
                    {"term": "barbershop", "count": 4},
                    {"term": "interview prep", "count": 3},
                ],
                "included_examples": {
                    "youtube_watch": [{"label": "How to Grow a Barbershop", "count": 2}],
                },
            }
        },
    )
    interest_facets, media_facets = brain_atlas._interest_and_media_facets(
        [{"source_item": source_item, "sync_source": None, "project_note": None}],
        now=datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
    )

    assert interest_facets[0].facet_type == "interests"
    assert interest_facets[0].title == "barbershop"
    assert media_facets[0].facet_type == "media"
    assert "Barbershop" in media_facets[0].title


def test_build_links_uses_story_connections_and_story_overlap() -> None:
    project = brain_atlas.BrainFacet(
        id="facet:project:1",
        facet_type="projects",
        title="duSraBheja",
        summary="Second brain project.",
        attention_score=0.9,
        recency_score=0.8,
        signal_kind="direct_agent",
        created_at_utc="2026-03-15T12:00:00+00:00",
        happened_at_utc="2026-03-15T12:00:00+00:00",
        created_at_local="2026-03-15 08:00 AM EDT",
        happened_at_local="2026-03-15 08:00 AM EDT",
        display_timezone="America/New_York",
    )
    story = brain_atlas.BrainFacet(
        id="facet:story:1",
        facet_type="stories",
        title="Design direction",
        summary="duSraBheja is shifting into a visual Brain Atlas.",
        attention_score=0.8,
        recency_score=0.7,
        signal_kind="direct_agent",
        created_at_utc="2026-03-15T12:00:00+00:00",
        happened_at_utc="2026-03-15T12:00:00+00:00",
        created_at_local="2026-03-15 08:00 AM EDT",
        happened_at_local="2026-03-15 08:00 AM EDT",
        display_timezone="America/New_York",
    )
    connection = SimpleNamespace(
        id="conn-1",
        source_ref="duSraBheja",
        target_ref="Design direction",
        relation="co_signal",
        weight=0.9,
        evidence_count=3,
    )

    links = brain_atlas._build_links([project, story], [connection])

    assert any(link.relation == "co_signal" for link in links)


def test_story_river_filters_noisy_derived_entries() -> None:
    board = SimpleNamespace(
        id="board-1",
        board_type="daily",
        payload={"story": "Yesterday was light but intentional."},
        updated_at=datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
        coverage_end=datetime(2026, 3, 15, 3, 59, tzinfo=timezone.utc),
    )
    noisy = SimpleNamespace(
        id="event-1",
        entry_type="chrome_project_signal",
        actor_type="system",
        actor_name="collector",
        title="Chrome project signal: barbershop",
        summary="barbershop surfaced six times.",
        project_note_id=None,
        happened_at=datetime(2026, 3, 15, 14, 0, tzinfo=timezone.utc),
    )
    curated = SimpleNamespace(
        id="event-2",
        entry_type="progress_update",
        actor_type="agent",
        actor_name="codex",
        title="Atlas weighting pass",
        summary="Tightened current-headspace weighting.",
        project_note_id="project-1",
        happened_at=datetime(2026, 3, 15, 15, 0, tzinfo=timezone.utc),
    )

    events = brain_atlas._story_river_events(
        [board],
        [noisy, curated],
        now=datetime(2026, 3, 15, 16, 0, tzinfo=timezone.utc),
    )

    titles = [event.title for event in events]
    assert "Atlas weighting pass" in titles
    assert "Chrome project signal: barbershop" not in titles


def test_artifact_thought_facets_exclude_agent_generated_gap_items() -> None:
    item = {
        "artifact": SimpleNamespace(
            id="artifact-1",
            summary="Evidence gap: dataGenie",
            content_type="text",
            raw_text="Synthetic audit artifact",
            source="manual",
            metadata_={"capture_context": "agent_session"},
            created_at=datetime(2026, 3, 16, 4, 0, tzinfo=timezone.utc),
        ),
        "category": "note",
        "capture_intent": "thought",
        "validation_status": "accepted",
    }

    assert brain_atlas._artifact_facet(item) is None


def test_temporal_traversal_promotes_recent_connected_headspace() -> None:
    project = brain_atlas.BrainFacet(
        id="facet:project:1",
        facet_type="projects",
        title="duSraBheja",
        summary="Current working brain project.",
        attention_score=0.66,
        recency_score=0.82,
        signal_kind="direct_agent",
        created_at_utc="2026-03-16T12:00:00+00:00",
        happened_at_utc="2026-03-16T12:00:00+00:00",
        created_at_local="2026-03-16 08:00 AM EDT",
        happened_at_local="2026-03-16 08:00 AM EDT",
        display_timezone="America/New_York",
        metadata={"project_id": "project-1"},
        evidence=[
            {
                "title": "Latest progress",
                "summary": "Temporal traversal and persona work landed.",
                "signal_kind": "direct_agent",
                "event_time_utc": "2026-03-16T12:00:00+00:00",
                "happened_at_local": "2026-03-16 08:00 AM EDT",
            }
        ],
    )
    idea = brain_atlas.BrainFacet(
        id="facet:idea:1",
        facet_type="ideas",
        title="Persona packet",
        summary="Structured tone and taste layer.",
        attention_score=0.54,
        recency_score=0.7,
        signal_kind="direct_agent",
        created_at_utc="2026-03-16T11:00:00+00:00",
        happened_at_utc="2026-03-16T11:00:00+00:00",
        created_at_local="2026-03-16 07:00 AM EDT",
        happened_at_local="2026-03-16 07:00 AM EDT",
        display_timezone="America/New_York",
    )
    event = brain_atlas.StoryRiverEvent(
        id="story-river:event:1",
        title="Temporal memory pass",
        summary="duSraBheja and persona packet were the main focus.",
        event_type="progress_update",
        signal_kind="direct_agent",
        happened_at_utc="2026-03-16T12:30:00+00:00",
        happened_at_local="2026-03-16 08:30 AM EDT",
        event_day_label="Sun, Mar 16",
        metadata={"project_note_id": "project-1", "related_refs": ["Persona packet"]},
    )
    links = [
        brain_atlas.FacetLink(
            source_id="facet:project:1",
            target_id="facet:idea:1",
            relation="contextual_overlap",
            weight=0.72,
            evidence_count=1,
            reason="Project and persona work are linked.",
        )
    ]

    current_headspace, memory_paths, scores = brain_atlas._build_temporal_traversal(
        [project, idea],
        [event],
        links,
        now=datetime(2026, 3, 16, 13, 0, tzinfo=timezone.utc),
    )

    assert current_headspace[0].facet_id == "facet:project:1"
    assert any(node.facet_id == "facet:idea:1" for node in current_headspace)
    assert memory_paths[0].related_facet_ids[0] == "facet:project:1"
    assert scores["facet:project:1"] > scores["facet:idea:1"]


def test_temporal_traversal_excludes_low_signal_sync_thoughts_from_headspace() -> None:
    noisy = brain_atlas.BrainFacet(
        id="facet:thought:1",
        facet_type="thoughts",
        title="Agent todo signal",
        summary="Workspace summary and checklist for old sync noise.",
        attention_score=0.82,
        recency_score=0.88,
        signal_kind="direct_sync",
        created_at_utc="2026-03-16T12:00:00+00:00",
        happened_at_utc="2026-03-16T12:00:00+00:00",
        created_at_local="2026-03-16 08:00 AM EDT",
        happened_at_local="2026-03-16 08:00 AM EDT",
        display_timezone="America/New_York",
    )
    meaningful = brain_atlas.BrainFacet(
        id="facet:thought:2",
        facet_type="thoughts",
        title="Interview pressure",
        summary="Interview prep and job-search anxiety have been active.",
        attention_score=0.72,
        recency_score=0.86,
        signal_kind="direct_human",
        created_at_utc="2026-03-16T11:30:00+00:00",
        happened_at_utc="2026-03-16T11:30:00+00:00",
        created_at_local="2026-03-16 07:30 AM EDT",
        happened_at_local="2026-03-16 07:30 AM EDT",
        display_timezone="America/New_York",
    )

    current_headspace, _, _ = brain_atlas._build_temporal_traversal(
        [noisy, meaningful],
        [],
        [],
        now=datetime(2026, 3, 16, 13, 0, tzinfo=timezone.utc),
    )

    facet_ids = [node.facet_id for node in current_headspace]
    assert "facet:thought:2" in facet_ids
    assert "facet:thought:1" not in facet_ids
