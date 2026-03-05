"""ARQ worker entrypoint + job enqueue helpers."""

import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from src.config import settings

log = logging.getLogger("brain-worker")

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


# ── Enqueue helpers (called from the bot) ────────────────────────

async def enqueue_ingest(
    discord_message_id: str | None,
    discord_channel_id: str,
    text: str,
    attachments: list[dict],
    force_category: str | None = None,
    source: str = "discord",
):
    pool = await get_pool()
    await pool.enqueue_job(
        "process_inbox_message",
        discord_message_id=discord_message_id,
        discord_channel_id=discord_channel_id,
        text=text,
        attachments=attachments,
        force_category=force_category,
        source=source,
    )


async def enqueue_reclassify(artifact_id: str, user_answer: str):
    pool = await get_pool()
    await pool.enqueue_job("reclassify_artifact", artifact_id=artifact_id, user_answer=user_answer)


# ── Worker class (ARQ entrypoint) ───────────────────────────────

class WorkerSettings:
    """ARQ worker settings — discovered by `arq src.worker.main.WorkerSettings`."""

    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    functions = [
        "src.worker.tasks.ingest.process_inbox_message",
        "src.worker.tasks.classify.classify_artifact",
        "src.worker.tasks.classify.reclassify_artifact",
        "src.worker.tasks.clarify.ask_clarification",
        "src.worker.tasks.embed.generate_embeddings",
        "src.worker.tasks.librarian.process_librarian",
    ]

    max_jobs = 5
    job_timeout = 300  # 5 minutes per job


if __name__ == "__main__":
    import asyncio
    from arq import run_worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    asyncio.run(run_worker(WorkerSettings))
