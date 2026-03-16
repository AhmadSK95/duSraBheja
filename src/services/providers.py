"""Provider registry and model-role bindings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from src.config import settings


class ProviderSpec(BaseModel):
    name: str
    kind: str = Field(description="anthropic, openai, openai_compatible, or ollama")
    api_key_env: str | None = None
    base_url: str | None = None
    models: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelRoleBinding(BaseModel):
    role: str
    provider: str
    model: str
    fallback_providers: list[str] = Field(default_factory=list)
    visibility: str = "private"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderRegistry(BaseModel):
    providers: list[ProviderSpec] = Field(default_factory=list)
    roles: list[ModelRoleBinding] = Field(default_factory=list)

    def provider_map(self) -> dict[str, ProviderSpec]:
        return {provider.name: provider for provider in self.providers}

    def role_map(self) -> dict[str, ModelRoleBinding]:
        return {binding.role: binding for binding in self.roles}


def _providers_config_path() -> Path:
    return Path(settings.providers_config_path).expanduser()


def _default_registry() -> ProviderRegistry:
    providers = [
        ProviderSpec(
            name="anthropic",
            kind="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            models={
                "classifier": settings.classifier_model,
                "reasoning": settings.sonnet_model,
                "merge": settings.sonnet_model,
                "public_chat": settings.sonnet_model,
            },
        ),
        ProviderSpec(
            name="openai",
            kind="openai",
            api_key_env="OPENAI_API_KEY",
            base_url=settings.default_openai_base_url or None,
            models={
                "embed": settings.embedding_model,
                "transcribe": settings.whisper_model,
                "web_research": settings.openai_web_search_model,
            },
        ),
    ]
    roles = [
        ModelRoleBinding(role="classifier", provider="anthropic", model=settings.classifier_model),
        ModelRoleBinding(role="reasoning", provider="anthropic", model=settings.sonnet_model),
        ModelRoleBinding(role="merge", provider="anthropic", model=settings.sonnet_model),
        ModelRoleBinding(role="embed", provider="openai", model=settings.embedding_model),
        ModelRoleBinding(role="transcribe", provider="openai", model=settings.whisper_model),
        ModelRoleBinding(role="public_chat", provider="anthropic", model=settings.sonnet_model, visibility="public"),
        ModelRoleBinding(role="web_research", provider="openai", model=settings.openai_web_search_model),
    ]
    if settings.opus_model:
        roles.append(
            ModelRoleBinding(
                role="reasoning_heavy",
                provider="anthropic",
                model=settings.opus_model,
            )
        )
    return ProviderRegistry(providers=providers, roles=roles)


def _registry_from_file(path: Path) -> ProviderRegistry | None:
    if not path.exists():
        return None
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    providers = [ProviderSpec.model_validate(item) for item in payload.get("providers") or []]
    roles = [ModelRoleBinding.model_validate(item) for item in payload.get("roles") or []]
    if not providers or not roles:
        return None
    return ProviderRegistry(providers=providers, roles=roles)


@lru_cache(maxsize=1)
def load_provider_registry() -> ProviderRegistry:
    return _registry_from_file(_providers_config_path()) or _default_registry()


def clear_provider_registry_cache() -> None:
    load_provider_registry.cache_clear()


def binding_for_role(role: str) -> ModelRoleBinding:
    registry = load_provider_registry()
    binding = registry.role_map().get(role)
    if binding:
        return binding
    fallback_role = "reasoning" if role not in {"embed", "transcribe", "web_research"} else role
    fallback = registry.role_map().get(fallback_role)
    if fallback:
        return fallback
    raise KeyError(f"Model role binding not found for role '{role}'")


def provider_for_role(role: str) -> ProviderSpec:
    binding = binding_for_role(role)
    provider = load_provider_registry().provider_map().get(binding.provider)
    if not provider:
        raise KeyError(f"Provider '{binding.provider}' not found for role '{role}'")
    return provider


def model_for_role(role: str) -> str:
    return binding_for_role(role).model


def provider_registry_summary() -> dict[str, Any]:
    registry = load_provider_registry()
    return {
        "providers": [provider.model_dump(mode="json") for provider in registry.providers],
        "roles": [binding.model_dump(mode="json") for binding in registry.roles],
    }
