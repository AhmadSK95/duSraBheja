"""Clarify task — generate moderation prompts for the private dashboard queue."""

import logging
import uuid

from src.database import async_session
from src.agents.clarifier import generate_question
from src.lib.store import create_review, get_artifact

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

        validation_status = getattr(classification, "validation_status", "validated")
        quality_issues = list(getattr(classification, "quality_issues", []) or [])
        if validation_status != "validated" and quality_issues:
            question = "Review this capture before it affects boards or project state.\n\n" + "\n".join(
                f"- {issue.get('message') or issue.get('code')}"
                for issue in quality_issues[:5]
            )
            review_kind = "validation"
        else:
            question = await generate_question(
                session,
                original_text=artifact.raw_text,
                classification_attempt=classification_data,
                trace_id=trace_id,
            )
            review_kind = "classification"

        # Create review queue entry
        review = await create_review(
            session,
            artifact_id=artifact_uuid,
            classification_id=classification_uuid,
            question=question,
            review_kind=review_kind,
        )

        log.info(f"Created review {review.id} for artifact {artifact_id}: {question}")
