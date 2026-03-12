#!/usr/bin/env python3
"""Backfill structured conversation sessions from previously imported source items."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database import async_session  # noqa: E402
from src.services.conversation_backfill import backfill_conversation_sessions  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill conversation sessions from imported source items")
    parser.add_argument(
        "--source-type",
        action="append",
        dest="source_types",
        help="Limit the repair to one or more sync source types",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    async with async_session() as session:
        result = await backfill_conversation_sessions(
            session,
            source_types=args.source_types,
        )
    print(json.dumps(result, indent=2))
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
