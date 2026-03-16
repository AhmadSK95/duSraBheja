from __future__ import annotations

import argparse
import asyncio
import json

from src.database import async_session
from src.services.library_cleanup import apply_library_cleanup, build_library_cleanup_preview


async def _main(args: argparse.Namespace) -> int:
    async with async_session() as session:
        if args.apply:
            payload = {"mode": "apply", **(await apply_library_cleanup(session, limit=args.limit))}
        else:
            payload = {"mode": "preview", **(await build_library_cleanup_preview(session, limit=args.limit))}
    print(json.dumps(payload, indent=2, default=str))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote canonical memory, then prune legacy low-signal records")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
