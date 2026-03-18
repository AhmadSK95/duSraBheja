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
JOB_GENERATE_DAILY_BOARD = "src.worker.tasks.boards.generate_daily_board"
JOB_GENERATE_WEEKLY_BOARD = "src.worker.tasks.boards.generate_weekly_board"
JOB_GENERATE_DAILY_DIGEST = "src.worker.tasks.digest.generate_daily_digest"
JOB_GENERATE_SCHEDULED_DIGEST_TICK = "src.worker.tasks.digest.generate_scheduled_digest_tick"
JOB_PROCESS_DUE_REMINDERS = "src.worker.tasks.reminders.process_due_reminders"
JOB_GENERATE_KNOWLEDGE_REFRESH = "src.worker.tasks.knowledge.generate_knowledge_refresh"
JOB_REFRESH_VOICE_PROFILE = "src.worker.tasks.voice.refresh_voice_profile_task"
JOB_RUN_CONTINUOUS_COGNITION = "src.worker.tasks.cognition.run_continuous_cognition_task"
JOB_REFRESH_WEBSITE_TASTE = "src.worker.tasks.website.refresh_website_taste_task"

EVENT_ARTIFACT_PROCESSED = "brain:artifact_processed"
EVENT_REVIEW_CREATED = "brain:review_created"
EVENT_ARTIFACT_FAILED = "brain:artifact_failed"
EVENT_BOARD_READY = "brain:board_ready"
EVENT_DIGEST_READY = "brain:digest_ready"
EVENT_SYNC_COMPLETED = "brain:sync_completed"
EVENT_REMINDER_DUE = "brain:reminder_due"


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


async def enqueue_story_pulse_digest(*, reason: str, metadata: dict | None = None) -> bool:
    pool = await get_pool()
    cooldown_seconds = max(60, settings.digest_story_pulse_cooldown_minutes * 60)
    debounce_key = "brain:digest:story_pulse_lock"
    acquired = await pool.set(debounce_key, reason, ex=cooldown_seconds, nx=True)
    if not acquired:
        return False
    await pool.enqueue_job(
        JOB_GENERATE_DAILY_DIGEST,
        trigger="story_pulse",
        reason=reason,
        metadata=metadata or {},
    )
    return True


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
        JOB_GENERATE_DAILY_BOARD,
        JOB_GENERATE_WEEKLY_BOARD,
        JOB_GENERATE_DAILY_DIGEST,
        JOB_GENERATE_SCHEDULED_DIGEST_TICK,
        JOB_PROCESS_DUE_REMINDERS,
        JOB_GENERATE_KNOWLEDGE_REFRESH,
        JOB_REFRESH_VOICE_PROFILE,
        JOB_RUN_CONTINUOUS_COGNITION,
        JOB_REFRESH_WEBSITE_TASTE,
    ]

    cron_jobs = [
        cron(
            JOB_GENERATE_SCHEDULED_DIGEST_TICK,
            hour=set(range(24)),
            minute=0,
        ),
        cron(
            JOB_PROCESS_DUE_REMINDERS,
            hour=set(range(24)),
            minute=set(range(60)),
        ),
        cron(
            JOB_GENERATE_KNOWLEDGE_REFRESH,
            hour=set(range(0, 24, max(1, settings.knowledge_refresh_hours))),
            minute=15,
        ),
        cron(
            JOB_RUN_CONTINUOUS_COGNITION,
            hour=set(range(0, 24, max(1, settings.cognition_refresh_hours))),
            minute=35,
        ),
        cron(
            JOB_REFRESH_VOICE_PROFILE,
            hour={settings.voice_refresh_hour},
            minute=45,
        ),
        # Website taste refresh — weekly on Sundays at 3:15 AM
        cron(
            JOB_REFRESH_WEBSITE_TASTE,
            weekday={6},
            hour={3},
            minute=15,
        ),
    ]

    max_jobs = 5
    job_timeout = 300  # 5 minutes per job


if __name__ == "__main__":
    import asyncio
    from arq import run_worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    asyncio.run(run_worker(WorkerSettings))
