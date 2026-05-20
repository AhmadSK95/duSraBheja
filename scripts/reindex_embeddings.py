"""One-shot reindex of all chunk embeddings.

Run this once after migrating to the new NVIDIA NIM embedding model
(default `nvidia/nv-embedqa-e5-v5`, 1024-dim). The script:

  * scans every Chunk row
  * skips rows whose `embedding_model` already matches the current setting
  * batches re-embeds the rest (default batch size 64)
  * writes the new vector + records the model used

Usage:
    uv run python scripts/reindex_embeddings.py [--batch 64] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Iterable

from sqlalchemy import func, select

from src.config import settings
from src.database import async_session
from src.lib.embeddings import embed_batch
from src.models import Chunk


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def _count_total(session) -> int:
    result = await session.execute(select(func.count()).select_from(Chunk))
    return int(result.scalar() or 0)


async def _count_pending(session, target_model: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(Chunk)
        .where((Chunk.embedding_model != target_model) | (Chunk.embedding_model.is_(None)))
    )
    return int(result.scalar() or 0)


async def reindex(batch_size: int, dry_run: bool) -> int:
    target_model = settings.embedding_model
    started = time.monotonic()
    processed = 0

    async with async_session() as session:
        total = await _count_total(session)
        pending = await _count_pending(session, target_model)
        print(
            f"Reindex target model: {target_model} ({settings.embedding_dimensions}d). "
            f"Total chunks: {total}. Pending: {pending}."
        )
        if dry_run:
            print("[dry-run] no writes performed.")
            return pending

    while True:
        async with async_session() as session:
            result = await session.execute(
                select(Chunk)
                .where(
                    (Chunk.embedding_model != target_model) | (Chunk.embedding_model.is_(None))
                )
                .order_by(Chunk.created_at.asc())
                .limit(batch_size)
            )
            chunks = list(result.scalars().all())
            if not chunks:
                break

            texts = [chunk.content or "" for chunk in chunks]
            vectors = await embed_batch(texts)
            if len(vectors) != len(chunks):
                raise RuntimeError(
                    f"NIM returned {len(vectors)} vectors for {len(chunks)} inputs"
                )
            for chunk, vector in zip(chunks, vectors):
                chunk.embedding = vector
                chunk.embedding_model = target_model
            await session.commit()
            processed += len(chunks)
            elapsed = time.monotonic() - started
            rate = processed / elapsed if elapsed > 0 else 0.0
            print(f"  processed {processed} ({rate:.1f}/s)")

    print(f"Done. Re-embedded {processed} chunks in {time.monotonic() - started:.1f}s.")
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description="Reindex chunk embeddings via NVIDIA NIM.")
    parser.add_argument("--batch", type=int, default=64, help="Embeddings per API call")
    parser.add_argument("--dry-run", action="store_true", help="Count only; no writes")
    args = parser.parse_args()

    if not settings.nvidia_api_key:
        print("ERROR: NVIDIA_API_KEY is not set in the environment.", file=sys.stderr)
        sys.exit(2)

    asyncio.run(reindex(batch_size=args.batch, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
