from __future__ import annotations

from src.agents import retriever


def test_build_system_prompt_includes_voice_instructions(monkeypatch) -> None:
    monkeypatch.setattr(
        retriever.settings,
        "brain_voice_instructions",
        "Write like Ahmad: direct, thoughtful, low-fluff, founder-operator energy.",
    )

    prompt = retriever.build_system_prompt()

    assert "Match Ahmad's voice and tone" in prompt
    assert "founder-operator energy" in prompt


def test_build_system_prompt_omits_voice_line_when_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(retriever.settings, "brain_voice_instructions", "")

    prompt = retriever.build_system_prompt()

    assert "Match Ahmad's voice and tone" not in prompt
