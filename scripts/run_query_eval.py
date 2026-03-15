#!/usr/bin/env python3
"""Run the stored retrieval reliability evaluation harness."""

from __future__ import annotations

import argparse
import asyncio
import json

from src.database import async_session
from src.services.evaluation import run_query_eval


async def _main(rounds: int, run_name: str) -> int:
    async with async_session() as session:
        result = await run_query_eval(session, rounds=rounds, run_name=run_name)
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the retrieval reliability eval harness.")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--run-name", default="retrieval-reliability")
    args = parser.parse_args()
    return asyncio.run(_main(args.rounds, args.run_name))


if __name__ == "__main__":
    raise SystemExit(main())
