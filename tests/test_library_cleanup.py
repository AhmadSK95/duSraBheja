from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import library_cleanup


@pytest.mark.asyncio
async def test_build_library_cleanup_preview_counts_legacy_sources_and_journals(monkeypatch) -> None:
    source_item = SimpleNamespace(
        id=uuid4(),
        title="Old collector dump",
        payload={"entry_type": "context_dump"},
    )
    project_note = SimpleNamespace(title="duSraBheja")
    journal_entry = SimpleNamespace(
        id=uuid4(),
        title="Blind spot",
        entry_type="blind_spot",
    )

    async def fake_sync_canonical_library(session):
        return {"threads": 1}

    async def fake_list_source_cleanup_candidates(session, *, source_types=None, entry_types=None, limit=1000):
        if source_types == ["collector"]:
            return [
                {
                    "source_item": source_item,
                    "sync_source": SimpleNamespace(source_type="collector"),
                    "project_note": project_note,
                }
            ]
        return []

    async def fake_list_journal_cleanup_candidates(session, *, entry_types=None, older_than_days=None, limit=1000):
        return [{"journal_entry": journal_entry, "project_note": project_note}]

    monkeypatch.setattr(library_cleanup, "sync_canonical_library", fake_sync_canonical_library)
    monkeypatch.setattr(library_cleanup.store, "list_source_cleanup_candidates", fake_list_source_cleanup_candidates)
    monkeypatch.setattr(library_cleanup.store, "list_journal_cleanup_candidates", fake_list_journal_cleanup_candidates)

    payload = await library_cleanup.build_library_cleanup_preview(object(), limit=50)

    assert payload["candidate_count"] == 2
    assert payload["source_candidate_count"] == 1
    assert payload["journal_candidate_count"] == 1
    assert payload["by_source_type"]["collector"] == 1
    assert payload["by_source_type"]["journal_entry"] == 1
    assert payload["by_entry_type"]["context_dump"] == 1
    assert payload["by_entry_type"]["blind_spot"] == 1


@pytest.mark.asyncio
async def test_apply_library_cleanup_prunes_and_rebuilds(monkeypatch) -> None:
    project_id = uuid4()
    source_item_id = uuid4()
    journal_entry_id = uuid4()
    touched = {}

    async def fake_sync_canonical_library(session):
        touched["sync_calls"] = touched.get("sync_calls", 0) + 1
        return {"threads": 2, "episodes": 3}

    async def fake_list_source_cleanup_candidates(session, *, source_types=None, entry_types=None, limit=1000):
        if source_types == ["collector"]:
            return [
                {
                    "source_item": SimpleNamespace(id=source_item_id, title="Legacy dump", payload={"entry_type": "context_dump"}),
                    "sync_source": SimpleNamespace(source_type="collector"),
                    "project_note": SimpleNamespace(title="duSraBheja"),
                }
            ]
        return []

    async def fake_list_journal_cleanup_candidates(session, *, entry_types=None, older_than_days=None, limit=1000):
        return [{"journal_entry": SimpleNamespace(id=journal_entry_id, title="Blind spot", entry_type="blind_spot"), "project_note": None}]

    async def fake_purge_source_items(session, *, source_item_ids):
        touched["source_item_ids"] = source_item_ids
        return {"project_note_ids_touched": [str(project_id)], "source_items_deleted": len(source_item_ids)}

    async def fake_purge_journal_entries(session, *, journal_entry_ids):
        touched["journal_entry_ids"] = journal_entry_ids
        return {"project_note_ids_touched": [], "journal_entries_deleted": len(journal_entry_ids)}

    async def fake_clear_story_connections(session, *, relation="co_signal"):
        touched["cleared_relation"] = relation

    async def fake_purge_orphans(session):
        touched["orphan_cleanup"] = True
        return {"orphaned_evidence_deleted": 2}

    async def fake_recompute(session, *, project_note_ids):
        touched["project_note_ids"] = project_note_ids

    monkeypatch.setattr(library_cleanup, "sync_canonical_library", fake_sync_canonical_library)
    monkeypatch.setattr(library_cleanup.store, "list_source_cleanup_candidates", fake_list_source_cleanup_candidates)
    monkeypatch.setattr(library_cleanup.store, "list_journal_cleanup_candidates", fake_list_journal_cleanup_candidates)
    monkeypatch.setattr(library_cleanup.store, "purge_source_items", fake_purge_source_items)
    monkeypatch.setattr(library_cleanup.store, "purge_journal_entries", fake_purge_journal_entries)
    monkeypatch.setattr(library_cleanup.store, "clear_story_connections", fake_clear_story_connections)
    monkeypatch.setattr(library_cleanup.store, "purge_orphaned_canonical_records", fake_purge_orphans)
    monkeypatch.setattr(library_cleanup, "recompute_project_states", fake_recompute)

    payload = await library_cleanup.apply_library_cleanup(object(), limit=25)

    assert touched["source_item_ids"] == [source_item_id]
    assert touched["journal_entry_ids"] == [journal_entry_id]
    assert touched["cleared_relation"] == "co_signal"
    assert touched["orphan_cleanup"] is True
    assert touched["project_note_ids"] == [project_id]
    assert payload["canonical_counts"]["threads"] == 2
    assert payload["candidate_count"] == 2
