from __future__ import annotations

from src.worker.extractors.image import _build_ocr_prompt


def test_build_ocr_prompt_includes_project_aliases() -> None:
    prompt = _build_ocr_prompt(["duSraBheja", "dataGenie", "teacherAI"])

    assert "Known active project names and aliases" in prompt
    assert "- duSraBheja" in prompt
    assert "- dataGenie" in prompt
    assert "bias toward the known project names" in prompt
