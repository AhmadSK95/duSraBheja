"""ARQ worker entrypoint + job enqueue helpers."""

import json
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
JOB_PROCESS_DUE_REMINDERS = "src.worker.tasks.reminders.process_due_reminders"
JOB_RUN_CONTINUOUS_COGNITION = "src.worker.tasks.cognition.run_continuous_cognition_task"
JOB_REFRESH_PUBLIC_SURFACE = "src.worker.tasks.public_surface.refresh_public_surface_task"

EVENT_ARTIFACT_PROCESSED = "brain:artifact_processed"
EVENT_REVIEW_CREATED = "brain:review_created"
EVENT_ARTIFACT_FAILED = "brain:artifact_failed"
EVENT_SYNC_COMPLETED = "brain:sync_completed"
EVENT_REMINDER_DUE = "brain:reminder_due"
EVENT_PUBLIC_SURFACE_REFRESH_COMPLETED = "brain:public_surface_refresh_completed"
EVENT_PUBLIC_SURFACE_REVIEW_CREATED = "brain:public_surface_review_created"
EVENT_PUBLIC_SURFACE_REVIEW_RESOLVED = "brain:public_surface_review_resolved"


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
    metadata: dict | None = None,
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
        metadata=metadata or {},
    )


async def enqueue_reclassify(artifact_id: str, user_answer: str):
    pool = await get_pool()
    await pool.enqueue_job(JOB_RECLASSIFY_ARTIFACT, artifact_id=artifact_id, user_answer=user_answer)


async def enqueue_classify(artifact_id: str, force_category: str | None = None):
    pool = await get_pool()
    await pool.enqueue_job(JOB_CLASSIFY_ARTIFACT, artifact_id=artifact_id, force_category=force_category)


async def publish_event(channel: str, payload: dict) -> None:
    pool = await get_pool()
    await pool.publish(channel, json.dumps(payload))


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
        JOB_PROCESS_DUE_REMINDERS,
        JOB_RUN_CONTINUOUS_COGNITION,
        JOB_REFRESH_PUBLIC_SURFACE,
    ]

    cron_jobs = [
        cron(
            JOB_PROCESS_DUE_REMINDERS,
            hour=set(range(24)),
            minute=set(range(60)),
        ),
        cron(
            JOB_REFRESH_PUBLIC_SURFACE,
            hour={settings.public_surface_refresh_hour},
            minute=settings.public_surface_refresh_minute,
        ),
    ]

    max_jobs = 5
    job_timeout = 300  # 5 minutes per job


if __name__ == "__main__":
    import asyncio

    from arq import run_worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    asyncio.run(run_worker(WorkerSettings))
