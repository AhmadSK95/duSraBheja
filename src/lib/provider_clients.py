"""Provider-aware API client helpers."""

from __future__ import annotations

import os
from functools import lru_cache

import openai

from src.config import settings
from src.services.providers import provider_for_role


def _provider_api_key(role: str) -> str:
    provider = provider_for_role(role)
    env_key = provider.api_key_env or ""
    env_value = os.getenv(env_key, "").strip() if env_key else ""
    if env_value:
        return env_value
    if provider.name == "openai":
        return settings.openai_api_key
    return settings.openai_api_key


@lru_cache(maxsize=16)
def openai_client_for_role(role: str) -> openai.AsyncOpenAI:
    provider = provider_for_role(role)
    base_url = (provider.base_url or "").strip() or None
    return openai.AsyncOpenAI(
        api_key=_provider_api_key(role) or "unused-local-key",
        base_url=base_url,
    )
