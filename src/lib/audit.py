"""Audit event logging — every AI call gets recorded."""

import uuid
from decimal import Decimal

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import AuditLog


async def log_event(
    session: AsyncSession,
    *,
    agent: str,
    action: str,
    model_used: str | None = None,
    input_summary: str | None = None,
    output_summary: str | None = None,
    tokens_used: int | None = None,
    cost_usd: Decimal | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    trace_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> None:
    """Insert an audit log entry."""
    await session.execute(
        insert(AuditLog).values(
            trace_id=trace_id or uuid.uuid4(),
            agent=agent,
            action=action,
            model_used=model_used,
            input_summary=input_summary[:500] if input_summary else None,
            output_summary=output_summary[:500] if output_summary else None,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            error=error,
            metadata_=metadata or {},
        )
    )
    await session.commit()
