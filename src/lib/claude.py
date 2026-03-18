"""Anthropic Claude SDK wrapper with model routing and cost tracking."""

import time
import uuid
from decimal import Decimal

import anthropic

from src.config import settings
from src.services.providers import model_for_role

# Cost per 1M tokens (approximate, as of 2026-03)
MODEL_COSTS = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}

client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    costs = MODEL_COSTS.get(model, {"input": 3.0, "output": 15.0})
    input_cost = (input_tokens / 1_000_000) * costs["input"]
    output_cost = (output_tokens / 1_000_000) * costs["output"]
    return Decimal(str(round(input_cost + output_cost, 6)))


async def call_claude(
    prompt: str,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Call Claude and return response with metadata for audit logging.

    Returns:
        {
            "text": str,
            "model": str,
            "input_tokens": int,
            "output_tokens": int,
            "cost_usd": Decimal,
            "duration_ms": int,
            "trace_id": UUID,
        }
    """
    model = model or model_for_role("reasoning")
    trace_id = trace_id or uuid.uuid4()
    start = time.monotonic()

    messages = [{"role": "user", "content": prompt}]
    kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages, "temperature": temperature}
    if system:
        kwargs["system"] = system

    response = await client.messages.create(**kwargs)

    duration_ms = int((time.monotonic() - start) * 1000)
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    return {
        "text": response.content[0].text,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": estimate_cost(model, input_tokens, output_tokens),
        "duration_ms": duration_ms,
        "trace_id": trace_id,
    }


async def call_claude_conversation(
    messages: list[dict],
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Call Claude with a multi-turn messages array.

    Args:
        messages: List of {role, content} dicts for multi-turn conversation.
        system: Optional system prompt.
        model: Model ID override.
        max_tokens: Max output tokens.
        temperature: Sampling temperature.
        trace_id: Optional trace ID for audit logging.

    Returns same dict shape as call_claude().
    """
    model = model or model_for_role("reasoning")
    trace_id = trace_id or uuid.uuid4()
    start = time.monotonic()

    kwargs: dict = {
        "model": model, "max_tokens": max_tokens,
        "messages": messages, "temperature": temperature,
    }
    if system:
        kwargs["system"] = system

    response = await client.messages.create(**kwargs)

    duration_ms = int((time.monotonic() - start) * 1000)
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    return {
        "text": response.content[0].text,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": estimate_cost(model, input_tokens, output_tokens),
        "duration_ms": duration_ms,
        "trace_id": trace_id,
    }


async def call_claude_vision(
    image_data: bytes,
    media_type: str,
    prompt: str,
    model: str | None = None,
    max_tokens: int = 4096,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Call Claude with an image (vision). Used for OCR."""
    import base64

    model = model or model_for_role("classifier")
    trace_id = trace_id or uuid.uuid4()
    start = time.monotonic()

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(image_data).decode()},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]

    response = await client.messages.create(model=model, max_tokens=max_tokens, messages=messages, temperature=0.0)

    duration_ms = int((time.monotonic() - start) * 1000)
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens

    return {
        "text": response.content[0].text,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": estimate_cost(model, input_tokens, output_tokens),
        "duration_ms": duration_ms,
        "trace_id": trace_id,
    }
