"""Base agent — shared Claude call wrapper with audit logging."""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.lib.audit import log_event
from src.lib.claude import call_claude, call_claude_vision


async def agent_call(
    session: AsyncSession,
    *,
    agent_name: str,
    action: str,
    prompt: str,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Call Claude and automatically log to audit trail.

    Returns the same dict as call_claude() plus audit logged.
    """
    trace_id = trace_id or uuid.uuid4()

    try:
        result = await call_claude(
            prompt=prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            trace_id=trace_id,
        )
        await log_event(
            session,
            agent=agent_name,
            action=action,
            model_used=result["model"],
            input_summary=prompt[:500],
            output_summary=result["text"][:500],
            tokens_used=result["input_tokens"] + result["output_tokens"],
            cost_usd=result["cost_usd"],
            duration_ms=result["duration_ms"],
            trace_id=trace_id,
        )
        return result

    except Exception as e:
        await log_event(
            session,
            agent=agent_name,
            action=action,
            model_used=model,
            input_summary=prompt[:500],
            error=str(e),
            trace_id=trace_id,
        )
        raise


async def agent_vision_call(
    session: AsyncSession,
    *,
    agent_name: str,
    action: str,
    image_data: bytes,
    media_type: str,
    prompt: str,
    model: str | None = None,
    max_tokens: int = 4096,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Call Claude vision and automatically log to audit trail."""
    trace_id = trace_id or uuid.uuid4()

    try:
        result = await call_claude_vision(
            image_data=image_data,
            media_type=media_type,
            prompt=prompt,
            model=model,
            max_tokens=max_tokens,
            trace_id=trace_id,
        )
        await log_event(
            session,
            agent=agent_name,
            action=action,
            model_used=result["model"],
            input_summary=f"[image:{media_type}] {prompt[:400]}",
            output_summary=result["text"][:500],
            tokens_used=result["input_tokens"] + result["output_tokens"],
            cost_usd=result["cost_usd"],
            duration_ms=result["duration_ms"],
            trace_id=trace_id,
        )
        return result

    except Exception as e:
        await log_event(
            session,
            agent=agent_name,
            action=action,
            model_used=model,
            input_summary=f"[image:{media_type}] {prompt[:400]}",
            error=str(e),
            trace_id=trace_id,
        )
        raise
