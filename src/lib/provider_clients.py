"""NVIDIA NIM client factory (OpenAI-compatible).

Single client across chat, vision, and embeddings. Kept here for callers that
expect a `*_client_for_role(role)` helper; under the hood it's just the same
NIM-pointed AsyncOpenAI instance used by src.lib.llm.
"""

from __future__ import annotations

from functools import lru_cache

import openai

from src.config import settings


@lru_cache(maxsize=1)
def nim_client() -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key=settings.nvidia_api_key or "unused",
        base_url=settings.nvidia_base_url,
    )


def openai_client_for_role(role: str) -> openai.AsyncOpenAI:  # noqa: ARG001 — kept for back-compat
    """Back-compat alias. Role is ignored; NIM serves every role."""
    return nim_client()
