from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from uuid import UUID

from src.database import async_session
from src.lib import store
from src.services.project_state import recompute_project_states

LEGACY_PRUNE_SPECS = {
    "collector": {
        "context_dump",
        "context_signal_dump",
        "directory_inventory",
    },
    "browser_activity": None,
    "codex_history": {
        "agent_memory_snapshot",
    },
    "claude_history": {
        "agent_memory_snapshot",
        "plan_snapshot",
        "todo_snapshot",
    },
}


async def _load_candidates(limit: int) -> list[dict]:
    candidates: list[dict] = []
    async with async_session() as session:
        for source_type, entry_types in LEGACY_PRUNE_SPECS.items():
            rows = await store.list_source_cleanup_candidates(
                session,
                source_types=[source_type],
                entry_types=sorted(entry_types) if entry_types else None,
                limit=limit,
            )
            if entry_types is None:
                rows = [
                    row
                    for row in rows
                    if row["sync_source"].source_type == source_type
                ]
            candidates.extend(rows)
    return candidates


def _preview_payload(candidates: list[dict]) -> dict:
    by_source = Counter()
    by_entry_type = Counter()
    sample_titles: list[dict] = []
    for row in candidates:
        source_item = row["source_item"]
        sync_source = row["sync_source"]
        payload = dict(source_item.payload or {})
        entry_type = payload.get("entry_type") or "unknown"
        by_source[sync_source.source_type] += 1
        by_entry_type[entry_type] += 1
        if len(sample_titles) < 20:
            sample_titles.append(
                {
                    "source_type": sync_source.source_type,
                    "entry_type": entry_type,
                    "title": source_item.title,
                    "project": row["project_note"].title if row["project_note"] else None,
                }
            )
    return {
        "candidate_count": len(candidates),
        "by_source_type": dict(by_source),
        "by_entry_type": dict(by_entry_type),
        "samples": sample_titles,
    }


async def _apply(candidates: list[dict]) -> dict:
    source_item_ids = [row["source_item"].id for row in candidates]
    async with async_session() as session:
        result = await store.purge_source_items(session, source_item_ids=source_item_ids)
        touched = [UUID(value) for value in result["project_note_ids_touched"]]
        if touched:
            await recompute_project_states(session, project_note_ids=touched)
    return result


async def _main(args: argparse.Namespace) -> int:
    candidates = await _load_candidates(limit=args.limit)
    preview = _preview_payload(candidates)
    if not args.apply:
        print(json.dumps({"mode": "preview", **preview}, indent=2))
        return 0

    result = await _apply(candidates)
    print(json.dumps({"mode": "apply", **preview, **result}, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune legacy low-signal dump records from the brain")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
