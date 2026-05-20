"""NVIDIA NIM LLM wrapper — OpenAI-compatible, free-tier.

Provides call_llm / call_llm_conversation / call_llm_vision with the same
return shape the rest of the codebase expects:

    {
        "text": str,
        "model": str,
        "input_tokens": int,
        "output_tokens": int,
        "cost_usd": Decimal,     # always 0 for NIM free-tier
        "duration_ms": int,
        "trace_id": UUID,
    }
"""

from __future__ import annotations

import base64
import time
import uuid
from decimal import Decimal
from functools import lru_cache

import openai

from src.config import settings
from src.services.providers import model_for_role

_ZERO_COST = Decimal("0")


@lru_cache(maxsize=1)
def _client() -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key=settings.nvidia_api_key or "unused",
        base_url=settings.nvidia_base_url,
    )


def _usage_tokens(usage) -> tuple[int, int]:
    if usage is None:
        return 0, 0
    return int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0)


async def call_llm(
    prompt: str,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    trace_id: uuid.UUID | None = None,
) -> dict:
    model = model or model_for_role("reasoning")
    trace_id = trace_id or uuid.uuid4()
    start = time.monotonic()

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = await _client().chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    duration_ms = int((time.monotonic() - start) * 1000)
    input_tokens, output_tokens = _usage_tokens(response.usage)

    return {
        "text": response.choices[0].message.content or "",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _ZERO_COST,
        "duration_ms": duration_ms,
        "trace_id": trace_id,
    }


async def call_llm_conversation(
    messages: list[dict],
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Multi-turn conversation. `messages` is the OpenAI format already
    (role/content). If `system` is provided, it's prepended."""
    model = model or model_for_role("reasoning")
    trace_id = trace_id or uuid.uuid4()
    start = time.monotonic()

    payload: list[dict] = []
    if system:
        payload.append({"role": "system", "content": system})
    payload.extend(messages)

    response = await _client().chat.completions.create(
        model=model,
        messages=payload,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    duration_ms = int((time.monotonic() - start) * 1000)
    input_tokens, output_tokens = _usage_tokens(response.usage)

    return {
        "text": response.choices[0].message.content or "",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _ZERO_COST,
        "duration_ms": duration_ms,
        "trace_id": trace_id,
    }


async def call_llm_vision(
    image_data: bytes,
    media_type: str,
    prompt: str,
    model: str | None = None,
    max_tokens: int = 4096,
    trace_id: uuid.UUID | None = None,
) -> dict:
    """Image OCR / description via a NIM vision model (Llama 3.2 Vision)."""
    model = model or model_for_role("vision")
    trace_id = trace_id or uuid.uuid4()
    start = time.monotonic()

    image_b64 = base64.b64encode(image_data).decode()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                },
            ],
        }
    ]

    response = await _client().chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.0,
    )

    duration_ms = int((time.monotonic() - start) * 1000)
    input_tokens, output_tokens = _usage_tokens(response.usage)

    return {
        "text": response.choices[0].message.content or "",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _ZERO_COST,
        "duration_ms": duration_ms,
        "trace_id": trace_id,
    }


# Backwards-compat shims so any straggler imports of the old names keep working
# until the codebase is fully cut over. Safe to remove once Phase 3 is done.
call_claude = call_llm
call_claude_conversation = call_llm_conversation
call_claude_vision = call_llm_vision
