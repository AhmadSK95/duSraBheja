from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import public_surface


def test_derive_public_facts_from_markdown_finds_profile_and_project_content(tmp_path: Path) -> None:
    profile_path = tmp_path / "candidate_profile.md"
    profile_path.write_text(
        "\n".join(
            [
                "# Interview Notes",
                "## Who I Am",
                "Builder focused on AI systems and product execution.",
                "## Professional Background",
                "Worked across software, analytics, and systems thinking.",
                "## Technical Skills",
                "Python, FastAPI, AI systems, product engineering.",
            ]
        ),
        encoding="utf-8",
    )
    project_path = tmp_path / "project_case_studies.md"
    project_path.write_text(
        "\n".join(
            [
                "# Project Descriptions",
                "### duSraBheja - personal brain",
                "An AI memory system with Discord intake, retrieval, and project-state views.",
            ]
        ),
        encoding="utf-8",
    )

    profile_facts = public_surface._derive_public_facts_from_markdown(
        profile_path,
        profile_path.read_text(encoding="utf-8"),
    )
    project_facts = public_surface._derive_public_facts_from_markdown(
        project_path,
        project_path.read_text(encoding="utf-8"),
    )

    assert any(fact["fact_key"] == "profile:identity" for fact in profile_facts)
    assert any(fact["facet"] == "skills" for fact in profile_facts)
    assert any(fact["project_slug"] == "dusrabheja" for fact in project_facts)


@pytest.mark.asyncio
async def test_answer_public_question_rejects_generic_queries(monkeypatch) -> None:
    async def fake_verify_turnstile_token(*, token: str, remote_ip: str | None = None) -> dict:
        return {"ok": True, "detail": "ok"}

    monkeypatch.setattr(public_surface, "verify_turnstile_token", fake_verify_turnstile_token)

    response = await public_surface.answer_public_question(
        object(),
        question="What is the weather in New York today?",
        remote_ip="127.0.0.1",
        user_agent="pytest",
        turnstile_token="token",
    )

    assert response["ok"] is False
    assert response["status_code"] == 400


@pytest.mark.asyncio
async def test_answer_public_question_uses_relevant_approved_facts(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_verify_turnstile_token(*, token: str, remote_ip: str | None = None) -> dict:
        return {"ok": True, "detail": "ok"}

    async def fake_get_public_profile(_session) -> dict:
        return {"summary": "Ahmad builds systems that connect memory, products, and operations.", "source_refs": ["profile:identity"]}

    async def fake_list_public_projects(_session) -> list[dict]:
        return [{"slug": "dusrabheja", "title": "duSraBheja", "summary": "A brain OS for personal memory and project state."}]

    async def fake_list_public_faq(_session) -> list[dict]:
        return [{"question": "What is Ahmad building?", "answer": "He is building AI systems and products."}]

    async def fake_get_public_answer_policy(_session) -> dict:
        return {"instructions": "Stay grounded in approved public facts."}

    async def fake_select_relevant_public_facts(_session, *, question: str, limit: int = 8) -> list:
        assert "duSraBheja" in question
        return [
            SimpleNamespace(
                fact_key="project:dusrabheja:case-study",
                facet="projects",
                fact_type="project_case_study",
                title="duSraBheja",
                body="A second-brain product that captures evidence, retrieval, and project state for Ahmad.",
            )
        ]

    async def fake_call_claude(*, prompt: str, model: str, max_tokens: int, system: str = "", trace_id=None, temperature: float = 0.0) -> dict:
        captured["prompt"] = prompt
        captured["model"] = model
        return {"text": "duSraBheja is Ahmad's second-brain system for evidence-grounded memory and project state."}

    monkeypatch.setattr(public_surface, "verify_turnstile_token", fake_verify_turnstile_token)
    monkeypatch.setattr(public_surface, "get_public_profile", fake_get_public_profile)
    monkeypatch.setattr(public_surface, "list_public_projects", fake_list_public_projects)
    monkeypatch.setattr(public_surface, "list_public_faq", fake_list_public_faq)
    monkeypatch.setattr(public_surface, "get_public_answer_policy", fake_get_public_answer_policy)
    monkeypatch.setattr(public_surface, "select_relevant_public_facts", fake_select_relevant_public_facts)
    monkeypatch.setattr(public_surface, "call_claude", fake_call_claude)

    response = await public_surface.answer_public_question(
        object(),
        question="What is duSraBheja and why does Ahmad care about it?",
        remote_ip="127.0.0.1",
        user_agent="pytest",
        turnstile_token="token",
    )

    assert response["ok"] is True
    assert "Relevant approved facts" in captured["prompt"]
    assert "duSraBheja" in captured["prompt"]
    assert response["sources"]["facts"] == ["project:dusrabheja:case-study"]
