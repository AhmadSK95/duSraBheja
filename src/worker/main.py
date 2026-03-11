"""ARQ worker entrypoint + job enqueue helpers."""

import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.cron import cron

from src.config import settings

log = logging.getLogger("brain-worker")

_pool: ArqRedis | None = None

JOB_PROCESS_INBOX_MESSAGE = "src.worker.tasks.ingest.process_inbox_message"
JOB_CLASSIFY_ARTIFACT = "src.worker.tasks.classify.classify_artifact"
JOB_RECLASSIFY_ARTIFACT = "src.worker.tasks.classify.reclassify_artifact"
JOB_ASK_CLARIFICATION = "src.worker.tasks.clarify.ask_clarification"
JOB_GENERATE_EMBEDDINGS = "src.worker.tasks.embed.generate_embeddings"
JOB_PROCESS_LIBRARIAN = "src.worker.tasks.librarian.process_librarian"
JOB_GENERATE_DAILY_DIGEST = "src.worker.tasks.digest.generate_daily_digest"


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
        JOB_PROCESS_INBOX_MESSAGE,
        discord_message_id=discord_message_id,
        discord_channel_id=discord_channel_id,
        text=text,
        attachments=attachments,
        force_category=force_category,
        source=source,
    )


async def enqueue_reclassify(artifact_id: str, user_answer: str):
    pool = await get_pool()
    await pool.enqueue_job(JOB_RECLASSIFY_ARTIFACT, artifact_id=artifact_id, user_answer=user_answer)


# ── Worker class (ARQ entrypoint) ───────────────────────────────

class WorkerSettings:
    """ARQ worker settings — discovered by `arq src.worker.main.WorkerSettings`."""

    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    functions = [
        JOB_PROCESS_INBOX_MESSAGE,
        JOB_CLASSIFY_ARTIFACT,
        JOB_RECLASSIFY_ARTIFACT,
        JOB_ASK_CLARIFICATION,
        JOB_GENERATE_EMBEDDINGS,
        JOB_PROCESS_LIBRARIAN,
        JOB_GENERATE_DAILY_DIGEST,
    ]

    cron_jobs = [
        cron(
            JOB_GENERATE_DAILY_DIGEST,
            hour=settings.digest_cron_hour,
            minute=0,
        )
    ]

    max_jobs = 5
    job_timeout = 300  # 5 minutes per job


if __name__ == "__main__":
    import asyncio
    from arq import run_worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    asyncio.run(run_worker(WorkerSettings))
