"""Classify task — runs classifier agent, routes to next step."""

import logging
import uuid

from src.config import settings
from src.database import async_session
from src.agents.classifier import classify, reclassify
from src.lib.store import (
    get_artifact,
    create_classification,
)

log = logging.getLogger("brain-worker.classify")


async def classify_artifact(ctx, artifact_id: str, force_category: str | None = None):
    """Classify an artifact and route based on confidence."""
    artifact_uuid = uuid.UUID(artifact_id)
    trace_id = uuid.uuid4()

    async with async_session() as session:
        artifact = await get_artifact(session, artifact_uuid)
        if not artifact:
            log.error(f"Artifact {artifact_id} not found")
            return

        if not artifact.raw_text:
            log.warning(f"Artifact {artifact_id} has no text to classify")
            return

        # Classify
        if force_category:
            # Skip classification, use forced category
            result = {
                "category": force_category,
                "confidence": 1.0,
                "entities": [],
                "tags": [],
                "priority": "medium",
                "suggested_action": None,
                "summary": artifact.raw_text[:200],
                "_meta": {"model": "forced", "tokens_used": 0, "cost_usd": 0, "duration_ms": 0},
            }
        else:
            result = await classify(session, artifact.raw_text, trace_id=trace_id)

        log.info(
            f"Classified artifact {artifact_id}: "
            f"category={result['category']}, confidence={result['confidence']:.2f}"
        )

        # Store classification
        meta = result.pop("_meta", {})
        classification = await create_classification(
            session,
            artifact_id=artifact_uuid,
            category=result["category"],
            confidence=result["confidence"],
            entities=result.get("entities", []),
            tags=result.get("tags", []),
            priority=result.get("priority", "medium"),
            suggested_action=result.get("suggested_action"),
            model_used=meta.get("model", "unknown"),
            tokens_used=meta.get("tokens_used"),
            cost_usd=meta.get("cost_usd"),
            is_final=result["confidence"] >= settings.confidence_threshold,
        )

        # Update artifact summary
        artifact.summary = result.get("summary", artifact.raw_text[:200])
        await session.commit()

        # Route based on confidence
        from src.worker.main import get_pool

        pool = await get_pool()

        if result["confidence"] >= settings.confidence_threshold:
            # High confidence → embed + librarian + route to channel
            await pool.enqueue_job("generate_embeddings", artifact_id=artifact_id)
            await pool.enqueue_job(
                "process_librarian",
                artifact_id=artifact_id,
                classification_id=str(classification.id),
            )
            log.info(f"High confidence — routing artifact {artifact_id} to channel")
        else:
            # Low confidence → ask clarification
            await pool.enqueue_job(
                "ask_clarification",
                artifact_id=artifact_id,
                classification_id=str(classification.id),
            )
            log.info(f"Low confidence ({result['confidence']:.2f}) — requesting clarification")


async def reclassify_artifact(ctx, artifact_id: str, user_answer: str):
    """Re-classify after user provides clarification."""
    artifact_uuid = uuid.UUID(artifact_id)
    trace_id = uuid.uuid4()

    async with async_session() as session:
        artifact = await get_artifact(session, artifact_uuid)
        if not artifact:
            log.error(f"Artifact {artifact_id} not found for reclassification")
            return

        result = await reclassify(session, artifact.raw_text, user_answer, trace_id=trace_id)

        meta = result.pop("_meta", {})
        classification = await create_classification(
            session,
            artifact_id=artifact_uuid,
            category=result["category"],
            confidence=result["confidence"],
            entities=result.get("entities", []),
            tags=result.get("tags", []),
            priority=result.get("priority", "medium"),
            suggested_action=result.get("suggested_action"),
            model_used=meta.get("model", "unknown"),
            tokens_used=meta.get("tokens_used"),
            cost_usd=meta.get("cost_usd"),
            is_final=True,  # User-clarified = always final
        )

        artifact.summary = result.get("summary", artifact.raw_text[:200])
        await session.commit()

        # Route to embed + librarian
        from src.worker.main import get_pool

        pool = await get_pool()
        await pool.enqueue_job("generate_embeddings", artifact_id=artifact_id)
        await pool.enqueue_job(
            "process_librarian",
            artifact_id=artifact_id,
            classification_id=str(classification.id),
        )

        log.info(f"Reclassified artifact {artifact_id}: {result['category']} ({result['confidence']:.2f})")
