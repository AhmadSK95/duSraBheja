#!/usr/bin/env python3
"""Seed and refresh the public profile surface from approved facts."""

from __future__ import annotations

import argparse
import asyncio
import json

from src.database import async_session
from src.services.public_surface import refresh_public_snapshots, seed_public_facts_from_interview_prep


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Skip local markdown seeding and only rebuild snapshots from approved facts.",
    )
    parser.add_argument(
        "--approve",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Mark seeded facts as approved.",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict:
    async with async_session() as session:
        payload: dict[str, object] = {}
        if not args.skip_seed:
            payload["seed"] = await seed_public_facts_from_interview_prep(session, approve=bool(args.approve))
        payload["refresh"] = await refresh_public_snapshots(session, force=True)
        return payload


def main() -> None:
    args = build_parser().parse_args()
    payload = asyncio.run(_run(args))
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
