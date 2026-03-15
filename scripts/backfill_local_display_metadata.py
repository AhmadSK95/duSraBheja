#!/usr/bin/env python3
"""Backfill display-time metadata for historical boards and retrieval traces."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database import async_session  # noqa: E402
from src.lib import store  # noqa: E402
from src.lib.time import describe_event_time, format_display_datetime  # noqa: E402


def _normalize_reason_rows(rows: Iterable[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    for item in rows or []:
        row = dict(item or {})
        event_value = row.get("event_time_utc") or row.get("event_time_local")
        row.update(describe_event_time(event_value))
        row["event_time_display"] = format_display_datetime(event_value)
        normalized.append(row)
    return normalized


def _normalize_trace_payload(payload: dict | None) -> dict:
    updated = dict(payload or {})
    candidate_lists = dict(updated.get("candidate_lists") or {})
    for list_name, candidates in list(candidate_lists.items()):
        normalized: list[dict] = []
        for candidate in candidates or []:
            item = dict(candidate or {})
            event_value = item.get("event_time_utc") or item.get("event_time_local")
            item.update(describe_event_time(event_value))
            item["event_time_display"] = format_display_datetime(event_value)
            normalized.append(item)
        candidate_lists[list_name] = normalized
    updated["candidate_lists"] = candidate_lists
    updated["selected_evidence"] = _normalize_reason_rows(updated.get("selected_evidence") or [])
    return updated


async def main() -> None:
    async with async_session() as session:
        boards = await store.list_boards(session, limit=500)
        traces = await store.list_retrieval_traces(session, limit=500)

        for board in boards:
            payload = dict(board.payload or {})
            payload["display_timezone"] = "America/New_York"
            payload["included_source_reasons"] = _normalize_reason_rows(payload.get("included_source_reasons"))
            payload["excluded_source_reasons"] = _normalize_reason_rows(payload.get("excluded_source_reasons"))
            await store.upsert_board(
                session,
                board_type=board.board_type,
                generated_for_date=board.generated_for_date,
                coverage_start=board.coverage_start,
                coverage_end=board.coverage_end,
                payload=payload,
                source_artifact_ids=board.source_artifact_ids or [],
                excluded_artifact_ids=board.excluded_artifact_ids or [],
                status=board.status,
                discord_channel_name=board.discord_channel_name,
                discord_message_id=board.discord_message_id,
            )

        for trace in traces:
            await store.update_retrieval_trace(
                session,
                trace.id,
                payload=_normalize_trace_payload(trace.payload),
            )

    print(
        {
            "status": "ok",
            "boards_backfilled": len(boards),
            "traces_backfilled": len(traces),
            "display_timezone": "America/New_York",
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
