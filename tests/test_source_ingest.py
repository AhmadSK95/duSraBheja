from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from src.services import source_ingest


def test_ingest_source_entries_backfills_conversation_session_for_unchanged_source(monkeypatch) -> None:
    source_item_id = uuid4()
    project_id = uuid4()
    touched = {}

    async def fake_upsert_sync_source(session, source_type, name, status="active", config=None):
        return SimpleNamespace(id=uuid4())

    async def fake_start_sync_run(session, sync_source_id, mode, metadata_=None):
        return SimpleNamespace(id=uuid4())

    async def fake_resolve_project(session, **kwargs):
        return SimpleNamespace(id=project_id, title="duSraBheja")

    async def fake_infer_project_from_text(session, text):
        return None

    async def fake_ensure_project_aliases(session, **kwargs):
        touched["aliases"] = True

    async def fake_upsert_project_repo(session, **kwargs):
        touched["repo"] = True

    async def fake_get_source_item_by_external_id(session, sync_source_id, external_id):
        return SimpleNamespace(id=source_item_id, content_hash="same-hash", project_note_id=project_id)

    async def fake_upsert_conversation_session(session, **kwargs):
        touched["conversation_session"] = kwargs
        return SimpleNamespace(id=uuid4()), True

    async def fake_finish_sync_run(session, sync_run_id, status, items_seen, items_imported, error=None):
        touched["finish"] = {"status": status, "items_seen": items_seen, "items_imported": items_imported}
        return None

    async def fake_touch_sync_source(session, sync_source_id):
        touched["touch"] = str(sync_source_id)

    monkeypatch.setattr(source_ingest.store, "upsert_sync_source", fake_upsert_sync_source)
    monkeypatch.setattr(source_ingest.store, "start_sync_run", fake_start_sync_run)
    monkeypatch.setattr(source_ingest.store, "upsert_project_repo", fake_upsert_project_repo)
    monkeypatch.setattr(source_ingest.store, "get_source_item_by_external_id", fake_get_source_item_by_external_id)
    monkeypatch.setattr(source_ingest.store, "upsert_conversation_session", fake_upsert_conversation_session)
    monkeypatch.setattr(source_ingest.store, "finish_sync_run", fake_finish_sync_run)
    monkeypatch.setattr(source_ingest.store, "touch_sync_source", fake_touch_sync_source)
    monkeypatch.setattr(source_ingest, "resolve_project", fake_resolve_project)
    monkeypatch.setattr(source_ingest, "infer_project_from_text", fake_infer_project_from_text)
    monkeypatch.setattr(source_ingest, "ensure_project_aliases", fake_ensure_project_aliases)
    monkeypatch.setattr(source_ingest, "recompute_project_states", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(source_ingest, "index_artifact", lambda *args, **kwargs: asyncio.sleep(0))

    async def fake_publish_sync_event(result):
        touched["sync_event"] = result["source_type"]

    async def fake_trigger_story_pulse(reason, metadata=None):
        touched["story_pulse"] = reason

    import types

    source_ingest_module_sync = __import__("src.services.sync", fromlist=["_publish_sync_event"])
    monkeypatch.setattr(source_ingest_module_sync, "_publish_sync_event", fake_publish_sync_event)
    monkeypatch.setattr(source_ingest_module_sync, "_trigger_story_pulse", fake_trigger_story_pulse)

    result = asyncio.run(
        source_ingest.ingest_source_entries(
            object(),
            source_type="codex_history",
            source_name="codex",
            mode="bootstrap",
            device_name="macbook",
            entries=[
                {
                    "external_id": "codex:session:abc",
                    "project_ref": "duSraBheja",
                    "title": "Codex session: duSraBheja",
                    "summary": "Session summary",
                    "entry_type": "conversation_session",
                    "category": "project",
                    "body_markdown": "Session body",
                    "content_hash": "same-hash",
                    "metadata": {
                        "agent_kind": "codex",
                        "session_id": "abc",
                        "cwd": "/Users/moenuddeenahmadshaik/code/duSraBheja",
                        "participants": ["assistant", "user"],
                        "turn_count": 42,
                    },
                }
            ],
        )
    )

    assert result["items_imported"] == 0
    assert touched["conversation_session"]["source_item_id"] == source_item_id
    assert touched["conversation_session"]["session_id"] == "abc"
    assert touched["finish"]["items_seen"] == 1
