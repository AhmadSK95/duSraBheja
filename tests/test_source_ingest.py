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


def test_ingest_source_entries_prefers_entry_summary_for_story_entry(monkeypatch) -> None:
    source_item_id = uuid4()
    artifact_id = uuid4()
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
        return None

    async def fake_get_source_item_by_external_id(session, sync_source_id, external_id):
        return None

    async def fake_create_artifact(session, **kwargs):
        return SimpleNamespace(id=artifact_id)

    async def fake_create_classification(session, **kwargs):
        return None

    async def fake_upsert_source_item(session, **kwargs):
        return SimpleNamespace(id=source_item_id), True

    async def fake_upsert_conversation_session(session, **kwargs):
        touched["conversation_session"] = kwargs
        return SimpleNamespace(id=uuid4()), True

    async def fake_publish_story_entry(session, **kwargs):
        touched["summary"] = kwargs["summary"]
        return {"journal_entry": SimpleNamespace(id=uuid4())}

    async def fake_finish_sync_run(session, sync_run_id, status, items_seen, items_imported, error=None):
        return None

    async def fake_touch_sync_source(session, sync_source_id):
        return None

    monkeypatch.setattr(source_ingest.store, "upsert_sync_source", fake_upsert_sync_source)
    monkeypatch.setattr(source_ingest.store, "start_sync_run", fake_start_sync_run)
    monkeypatch.setattr(source_ingest.store, "create_artifact", fake_create_artifact)
    monkeypatch.setattr(source_ingest.store, "create_classification", fake_create_classification)
    monkeypatch.setattr(source_ingest.store, "get_source_item_by_external_id", fake_get_source_item_by_external_id)
    monkeypatch.setattr(source_ingest.store, "upsert_source_item", fake_upsert_source_item)
    monkeypatch.setattr(source_ingest.store, "upsert_conversation_session", fake_upsert_conversation_session)
    monkeypatch.setattr(source_ingest.store, "finish_sync_run", fake_finish_sync_run)
    monkeypatch.setattr(source_ingest.store, "touch_sync_source", fake_touch_sync_source)
    monkeypatch.setattr(source_ingest, "resolve_project", fake_resolve_project)
    monkeypatch.setattr(source_ingest, "infer_project_from_text", fake_infer_project_from_text)
    monkeypatch.setattr(source_ingest, "ensure_project_aliases", fake_ensure_project_aliases)
    monkeypatch.setattr(source_ingest, "recompute_project_states", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(source_ingest, "index_artifact", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(source_ingest, "publish_story_entry", fake_publish_story_entry)

    async def fake_publish_sync_event(result):
        return None

    async def fake_trigger_story_pulse(reason, metadata=None):
        return None

    async def fake_extract_story_fields(session, **kwargs):
        return {
            "subject_type": "project",
            "subject_ref": "duSraBheja",
            "entry_type": "session_closeout",
            "title": kwargs["title"],
            "summary": "body derived summary",
            "decision": None,
            "rationale": None,
            "constraint": None,
            "outcome": None,
            "impact": None,
            "open_question": None,
            "evidence_refs": [],
            "tags": [],
        }

    source_ingest_module_sync = __import__("src.services.sync", fromlist=["_publish_sync_event"])
    monkeypatch.setattr(source_ingest_module_sync, "_publish_sync_event", fake_publish_sync_event)
    monkeypatch.setattr(source_ingest_module_sync, "_trigger_story_pulse", fake_trigger_story_pulse)
    monkeypatch.setattr(source_ingest_module_sync, "_extract_story_fields", fake_extract_story_fields)

    result = asyncio.run(
        source_ingest.ingest_source_entries(
            object(),
            source_type="agent",
            source_name="codex",
            mode="sync",
            device_name="codex",
            entries=[
                {
                    "external_id": "agent:closeout:test",
                    "project_ref": "duSraBheja",
                    "title": "Codex closeout: duSraBheja",
                    "summary": "short structured summary",
                    "entry_type": "session_closeout",
                    "category": "project",
                    "body_markdown": "# full markdown body",
                    "content_hash": "hash-1",
                    "metadata": {"agent_kind": "codex", "session_id": "session-1"},
                }
            ],
        )
    )

    assert result["items_imported"] == 1
    assert touched["summary"] == "short structured summary"
    assert touched["conversation_session"]["transcript_excerpt"] == "short structured summary"


def test_ingest_source_entries_respects_entry_quality_flags(monkeypatch) -> None:
    touched = {}

    async def fake_upsert_sync_source(session, source_type, name, status="active", config=None):
        return SimpleNamespace(id=uuid4())

    async def fake_start_sync_run(session, sync_source_id, mode, metadata_=None):
        return SimpleNamespace(id=uuid4())

    async def fake_resolve_project(session, **kwargs):
        return None

    async def fake_infer_project_from_text(session, text):
        return None

    async def fake_get_source_item_by_external_id(session, sync_source_id, external_id):
        return None

    async def fake_create_artifact(session, **kwargs):
        return SimpleNamespace(id=uuid4())

    async def fake_create_classification(session, **kwargs):
        touched["classification"] = kwargs
        return None

    async def fake_upsert_source_item(session, **kwargs):
        return SimpleNamespace(id=uuid4()), True

    async def fake_publish_story_entry(session, **kwargs):
        return {"journal_entry": SimpleNamespace(id=uuid4())}

    async def fake_finish_sync_run(session, sync_run_id, status, items_seen, items_imported, error=None):
        return None

    async def fake_touch_sync_source(session, sync_source_id):
        return None

    async def fake_extract_story_fields(session, **kwargs):
        return {
            "subject_type": "topic",
            "subject_ref": None,
            "entry_type": kwargs.get("source_type") or "note",
            "title": kwargs["title"],
            "summary": kwargs["body_markdown"][:120],
            "decision": None,
            "rationale": None,
            "constraint": None,
            "outcome": None,
            "impact": None,
            "open_question": None,
            "evidence_refs": [],
            "tags": [],
        }

    monkeypatch.setattr(source_ingest.store, "upsert_sync_source", fake_upsert_sync_source)
    monkeypatch.setattr(source_ingest.store, "start_sync_run", fake_start_sync_run)
    monkeypatch.setattr(source_ingest.store, "get_source_item_by_external_id", fake_get_source_item_by_external_id)
    monkeypatch.setattr(source_ingest.store, "create_artifact", fake_create_artifact)
    monkeypatch.setattr(source_ingest.store, "create_classification", fake_create_classification)
    monkeypatch.setattr(source_ingest.store, "upsert_source_item", fake_upsert_source_item)
    monkeypatch.setattr(source_ingest.store, "finish_sync_run", fake_finish_sync_run)
    monkeypatch.setattr(source_ingest.store, "touch_sync_source", fake_touch_sync_source)
    monkeypatch.setattr(source_ingest, "resolve_project", fake_resolve_project)
    monkeypatch.setattr(source_ingest, "infer_project_from_text", fake_infer_project_from_text)
    monkeypatch.setattr(source_ingest, "ensure_project_aliases", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(source_ingest, "recompute_project_states", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(source_ingest, "index_artifact", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(source_ingest, "publish_story_entry", fake_publish_story_entry)

    source_ingest_module_sync = __import__("src.services.sync", fromlist=["_publish_sync_event"])
    monkeypatch.setattr(source_ingest_module_sync, "_publish_sync_event", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(source_ingest_module_sync, "_extract_story_fields", fake_extract_story_fields)

    asyncio.run(
        source_ingest.ingest_source_entries(
            object(),
            source_type="collector",
            source_name="mac-collector",
            mode="sync",
            device_name="macbook",
            entries=[
                {
                    "external_id": "collector:repo:test",
                    "title": "duSraBheja local repo signal",
                    "summary": "Curated repo signal",
                    "entry_type": "repo_signal_summary",
                    "category": "project",
                    "body_markdown": "# Local Repo Signal",
                    "content_hash": "hash-collector-signal",
                    "capture_intent": "reference",
                    "intent_confidence": 0.93,
                    "validation_status": "validated",
                    "quality_issues": [],
                    "eligible_for_boards": False,
                    "eligible_for_project_state": False,
                    "metadata": {"snapshot_kind": "repo_signal"},
                }
            ],
        )
    )

    assert touched["classification"]["capture_intent"] == "reference"
    assert touched["classification"]["intent_confidence"] == 0.93
    assert touched["classification"]["eligible_for_boards"] is False
    assert touched["classification"]["eligible_for_project_state"] is False
