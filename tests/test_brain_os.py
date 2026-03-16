from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services import brain_os


@pytest.mark.asyncio
async def test_build_brain_self_description_exposes_protocols_and_capabilities(monkeypatch) -> None:
    captured: list[dict] = []

    async def fake_upsert_capability_record(session, *, capability_key: str, **kwargs):
        captured.append({"capability_key": capability_key, **kwargs})
        return SimpleNamespace(
            capability_key=capability_key,
            title=kwargs["title"],
            summary=kwargs["summary"],
            protocol=kwargs["protocol"],
            visibility=kwargs["visibility"],
            payload=kwargs["payload"],
        )

    monkeypatch.setattr(brain_os.store, "upsert_capability_record", fake_upsert_capability_record)
    monkeypatch.setattr(brain_os.settings, "app_base_url", "https://brain.thisisrikisart.com")
    monkeypatch.setattr(brain_os.settings, "mcp_transport", "streamable-http")
    monkeypatch.setattr(brain_os.settings, "mcp_port", 8100)

    payload = await brain_os.build_brain_self_description(object())

    assert payload["protocols"]["http"]["base_url"] == "https://brain.thisisrikisart.com"
    assert payload["protocols"]["mcp"]["transport"] == "streamable-http"
    assert any(item["key"] == "security:secret-vault" for item in payload["capabilities"])
    assert any(item["capability_key"] == "workflow:agent-loop" for item in captured)
