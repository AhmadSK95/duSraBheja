"""Clarify task — generate question and post to Discord thread."""

import logging
import uuid

from src.database import async_session
from src.agents.clarifier import generate_question
from src.lib.store import get_artifact, create_review

log = logging.getLogger("brain-worker.clarify")


async def ask_clarification(ctx, artifact_id: str, classification_id: str):
    """Generate a clarification question and create a review queue entry.

    Note: Posting to Discord thread happens via a callback mechanism.
    The bot polls for pending reviews or we use a notification channel.
    """
    artifact_uuid = uuid.UUID(artifact_id)
    classification_uuid = uuid.UUID(classification_id)
    trace_id = uuid.uuid4()

    async with async_session() as session:
        artifact = await get_artifact(session, artifact_uuid)
        if not artifact:
            log.error(f"Artifact {artifact_id} not found")
            return

        # Get classification data for context
        from src.lib.store import get_final_classification

        classification = await session.get(
            __import__("src.models", fromlist=["Classification"]).Classification,
            classification_uuid,
        )
        if not classification:
            log.error(f"Classification {classification_id} not found")
            return

        classification_data = {
            "category": classification.category,
            "confidence": classification.confidence,
            "summary": artifact.summary or artifact.raw_text[:200],
        }

        # Generate question
        question = await generate_question(
            session,
            original_text=artifact.raw_text,
            classification_attempt=classification_data,
            trace_id=trace_id,
        )

        # Create review queue entry
        review = await create_review(
            session,
            artifact_id=artifact_uuid,
            classification_id=classification_uuid,
            question=question,
        )

        log.info(f"Created review {review.id} for artifact {artifact_id}: {question}")

        # Signal bot to create thread (via Redis pub/sub or polling)
        from src.worker.main import get_pool

        pool = await get_pool()
        import json

        await pool.pool.publish(
            "brain:review_created",
            json.dumps({
                "review_id": str(review.id),
                "artifact_id": artifact_id,
                "discord_message_id": artifact.discord_message_id,
                "discord_channel_id": artifact.discord_channel_id,
                "question": question,
            }),
        )
