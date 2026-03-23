from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.config import settings
from src.services import profile_narrative, public_surface


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


def test_configured_public_contact_entries_follow_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "public_contact_email", "ahmad2609.as@gmail.com")
    monkeypatch.setattr(settings, "public_contact_linkedin_url", "https://www.linkedin.com/in/moenuddeen-shaik/")
    monkeypatch.setattr(settings, "public_contact_instagram_url", "https://www.instagram.com/shaik.moen/")
    monkeypatch.setattr(settings, "public_contact_phone", "")
    monkeypatch.setattr(settings, "public_contact_discord_url", "")

    entries = public_surface._configured_public_contact_entries()

    assert [item["fact_key"] for item in entries] == [
        "contact:email",
        "contact:linkedin",
        "contact:instagram",
    ]
    assert entries[0]["metadata_"]["href"] == "mailto:ahmad2609.as@gmail.com"


def test_public_seed_path_falls_back_to_container_mount(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing-seed"
    mounted = tmp_path / "public-seed"
    mounted.mkdir()

    monkeypatch.setattr(settings, "public_profile_seed_path", str(missing))
    monkeypatch.setattr(profile_narrative, "Path", lambda value: mounted if value == "/public-seed" else Path(value))

    path = public_surface._public_seed_path()

    assert path == mounted


def test_admin_alias_redirects_to_dashboard_login() -> None:
    client = TestClient(app)
    response = client.get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/login"


@pytest.mark.asyncio
async def test_answer_public_question_rejects_generic_queries(monkeypatch) -> None:
    async def fake_verify_turnstile_token(*, token: str | None, remote_ip: str | None = None) -> dict:
        return {"ok": True, "detail": "ok"}

    monkeypatch.setattr(public_surface, "verify_turnstile_token", fake_verify_turnstile_token)
    monkeypatch.setattr(public_surface, "public_chat_captcha_enabled", lambda: True)

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

    async def fake_verify_turnstile_token(*, token: str | None, remote_ip: str | None = None) -> dict:
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
    monkeypatch.setattr(public_surface, "public_chat_captcha_enabled", lambda: True)
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


@pytest.mark.asyncio
async def test_answer_public_question_allows_no_captcha_mode(monkeypatch) -> None:
    async def fake_get_public_profile(_session) -> dict:
        return {"summary": "Ahmad builds systems.", "payload": {"identity_stack": [], "current_arc": {}, "eras": [], "capabilities": [], "proof_points": []}, "source_refs": []}

    async def fake_list_public_projects(_session) -> list[dict]:
        return []

    async def fake_list_public_faq(_session) -> list[dict]:
        return []

    async def fake_get_public_answer_policy(_session) -> dict:
        return {"instructions": "Stay grounded."}

    async def fake_select_relevant_public_facts(_session, *, question: str, limit: int = 8) -> list:
        return []

    async def fake_call_claude(*, prompt: str, model: str, max_tokens: int, system: str = "", trace_id=None, temperature: float = 0.0) -> dict:
        return {"text": "I build systems that connect memory, products, and operations."}

    monkeypatch.setattr(public_surface, "public_chat_captcha_enabled", lambda: False)
    monkeypatch.setattr(public_surface, "get_public_profile", fake_get_public_profile)
    monkeypatch.setattr(public_surface, "list_public_projects", fake_list_public_projects)
    monkeypatch.setattr(public_surface, "list_public_faq", fake_list_public_faq)
    monkeypatch.setattr(public_surface, "get_public_answer_policy", fake_get_public_answer_policy)
    monkeypatch.setattr(public_surface, "select_relevant_public_facts", fake_select_relevant_public_facts)
    monkeypatch.setattr(public_surface, "call_claude", fake_call_claude)

    response = await public_surface.answer_public_question(
        object(),
        question="What kind of engineer is Ahmad?",
        remote_ip="127.0.0.1",
        user_agent="pytest",
        turnstile_token=None,
    )

    assert response["ok"] is True


def test_cycle_report_uses_explicit_product_loop_stages() -> None:
    report = public_surface._build_cycle_report(
        cycle_number=6,
        wave_size=5,
        findings=[
            {"title": "About revamp", "summary": "About still needs a stronger editorial layout."},
            {"title": "Open Brain polish", "summary": "Open Brain still has layout opportunities."},
        ],
        qa=[{"name": f"qa-{idx}", "passed": True} for idx in range(10)],
        uat=[{"name": f"uat-{idx}", "passed": True} for idx in range(3)],
        staged_review=None,
        approval_required=False,
    )

    assert report["stages"][0]["stage"] == "product_design"
    assert report["stages"][3]["stage"] == "qa_rounds"
    assert report["stages"][4]["stage"] == "uat_rounds"
    assert report["design_brief"]["primary_problem"] == "About revamp"
