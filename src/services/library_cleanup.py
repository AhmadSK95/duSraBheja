"""Promotion-aware cleanup of legacy story-era records."""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import store
from src.services.library import sync_canonical_library
from src.services.project_state import recompute_project_states

SOURCE_PRUNE_SPECS: dict[str, set[str] | None] = {
    "collector": {
        "context_dump",
        "context_signal_dump",
        "directory_inventory",
    },
    "browser_activity": None,
    "codex_history": {
        "agent_memory_snapshot",
        "plan_snapshot",
        "todo_snapshot",
        "session_summary_generation",
    },
    "claude_history": {
        "agent_memory_snapshot",
        "plan_snapshot",
        "todo_snapshot",
        "session_summary_generation",
    },
}

JOURNAL_PRUNE_ENTRY_TYPES = {
    "blind_spot",
    "knowledge_refresh",
    "synapse",
    "research_thread",
}

JOURNAL_PRUNE_OLDER_THAN_DAYS = 10


async def _load_source_candidates(session: AsyncSession, *, limit: int) -> list[dict]:
    candidates: list[dict] = []
    for source_type, entry_types in SOURCE_PRUNE_SPECS.items():
        rows = await store.list_source_cleanup_candidates(
            session,
            source_types=[source_type],
            entry_types=sorted(entry_types) if entry_types else None,
            limit=limit,
        )
        if entry_types is None:
            rows = [row for row in rows if row["sync_source"].source_type == source_type]
        candidates.extend(rows)
    return candidates


async def _load_journal_candidates(session: AsyncSession, *, limit: int) -> list[dict]:
    return await store.list_journal_cleanup_candidates(
        session,
        entry_types=sorted(JOURNAL_PRUNE_ENTRY_TYPES),
        older_than_days=JOURNAL_PRUNE_OLDER_THAN_DAYS,
        limit=limit,
    )


def _preview_payload(source_candidates: list[dict], journal_candidates: list[dict]) -> dict[str, Any]:
    by_source_type = Counter()
    by_entry_type = Counter()
    samples: list[dict[str, Any]] = []

    for row in source_candidates:
        source_item = row["source_item"]
        sync_source = row["sync_source"]
        entry_type = str((source_item.payload or {}).get("entry_type") or "unknown")
        by_source_type[sync_source.source_type] += 1
        by_entry_type[entry_type] += 1
        if len(samples) < 20:
            samples.append(
                {
                    "kind": "source_item",
                    "source_type": sync_source.source_type,
                    "entry_type": entry_type,
                    "title": source_item.title,
                    "project": row["project_note"].title if row["project_note"] else None,
                }
            )

    for row in journal_candidates:
        entry = row["journal_entry"]
        by_source_type["journal_entry"] += 1
        by_entry_type[entry.entry_type] += 1
        if len(samples) < 40:
            samples.append(
                {
                    "kind": "journal_entry",
                    "source_type": "journal_entry",
                    "entry_type": entry.entry_type,
                    "title": entry.title,
                    "project": row["project_note"].title if row["project_note"] else None,
                }
            )

    return {
        "candidate_count": len(source_candidates) + len(journal_candidates),
        "source_candidate_count": len(source_candidates),
        "journal_candidate_count": len(journal_candidates),
        "by_source_type": dict(by_source_type),
        "by_entry_type": dict(by_entry_type),
        "samples": samples,
        "story_connections_reset": ["co_signal"],
    }


async def build_library_cleanup_preview(
    session: AsyncSession,
    *,
    limit: int = 5000,
) -> dict[str, Any]:
    await sync_canonical_library(session)
    source_candidates = await _load_source_candidates(session, limit=limit)
    journal_candidates = await _load_journal_candidates(session, limit=limit)
    return _preview_payload(source_candidates, journal_candidates)


async def apply_library_cleanup(
    session: AsyncSession,
    *,
    limit: int = 5000,
) -> dict[str, Any]:
    await sync_canonical_library(session)
    source_candidates = await _load_source_candidates(session, limit=limit)
    journal_candidates = await _load_journal_candidates(session, limit=limit)
    preview = _preview_payload(source_candidates, journal_candidates)

    source_item_ids = [row["source_item"].id for row in source_candidates]
    journal_entry_ids = [row["journal_entry"].id for row in journal_candidates]

    source_result = await store.purge_source_items(session, source_item_ids=source_item_ids)
    journal_result = await store.purge_journal_entries(session, journal_entry_ids=journal_entry_ids)
    await store.clear_story_connections(session, relation="co_signal")
    orphan_result = await store.purge_orphaned_canonical_records(session)

    touched_project_ids = {
        UUID(value)
        for value in [
            *(source_result.get("project_note_ids_touched") or []),
            *(journal_result.get("project_note_ids_touched") or []),
        ]
    }
    if touched_project_ids:
        await recompute_project_states(session, project_note_ids=sorted(touched_project_ids, key=str))

    canonical_counts = await sync_canonical_library(session)
    return {
        **preview,
        "source_cleanup": source_result,
        "journal_cleanup": journal_result,
        "orphan_cleanup": orphan_result,
        "canonical_counts": canonical_counts,
    }
