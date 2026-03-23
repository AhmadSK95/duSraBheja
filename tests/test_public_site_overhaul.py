from __future__ import annotations

import re
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from src.api.app import app
from src.api.routes import public as public_routes
from src.services.profile_narrative import build_profile_narrative, public_asset_path


class _DummyAsyncSession:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _fake_async_session() -> _DummyAsyncSession:
    return _DummyAsyncSession()


def _sample_profile() -> dict:
    narrative = build_profile_narrative()
    return {
        "title": narrative.get("name") or "Ahmad",
        "summary": narrative.get("hero_summary") or "",
        "payload": narrative,
        "refreshed_at": None,
        "source_refs": [],
    }


def _sample_projects() -> list[dict]:
    narrative = build_profile_narrative()
    return [
        {
            "slug": item.get("slug"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "payload": dict(item),
            "refreshed_at": None,
        }
        for item in narrative.get("projects") or []
    ]


def test_build_profile_narrative_exposes_overhaul_fields() -> None:
    narrative = build_profile_narrative()

    assert [item["slug"] for item in narrative["projects"][:6]] == [
        "dusrabheja",
        "datagenie",
        "balkan-barbershop-website",
        "kaffa-espresso-bar-website",
        "teachassist-ai",
        "ai-resume-matcher",
    ]
    assert narrative["resume_sections"]
    assert narrative["education"]
    assert narrative["skills"]
    assert narrative["currently"]
    assert narrative["open_brain_topics"]
    assert narrative["photo_slots"]
    assert narrative["taste_modules"]
    assert narrative["daily_update_window"]["items"]
    assert narrative["personal_signals"]["family"] == ["Annie", "Oscar", "Iris"]


def test_flagship_projects_expose_curated_case_study_payloads() -> None:
    narrative = build_profile_narrative()
    flagship = [item for item in narrative["projects"] if item["tier"] == "flagship"]

    for project in flagship:
        curated = dict(project.get("curated_case_study") or {})
        assert curated["problem"]
        assert curated["architecture_narrative"]
        assert curated["architecture_diagram"]["lanes"]
        assert curated["learnings"]
        assert project["supporting_evidence"]
        assert project["daily_update_window"]["items"]


def test_public_assets_resolve_for_curated_photo_slots_and_demos() -> None:
    narrative = build_profile_narrative()
    filenames = {
        slot["filename"]
        for slot in narrative.get("photo_slots") or []
        if slot.get("filename")
    }
    filenames.update(
        item.get("demo_asset")
        for item in narrative.get("projects") or []
        if item.get("demo_asset")
    )

    missing = [filename for filename in filenames if not public_asset_path(filename)]
    assert missing == []


@pytest.fixture
def client(monkeypatch) -> AsyncIterator[TestClient]:
    sample_profile = _sample_profile()
    sample_projects = _sample_projects()
    project_map = {item["slug"]: item for item in sample_projects}

    async def fake_get_public_profile(_session) -> dict:
        return sample_profile

    async def fake_list_public_projects(_session) -> list[dict]:
        return sample_projects

    async def fake_list_public_faq(_session) -> list[dict]:
        return sample_profile["payload"].get("faq") or []

    async def fake_get_public_answer_policy(_session) -> dict:
        return {"instructions": "Stay grounded in approved public facts."}

    async def fake_get_public_project(_session, slug: str) -> dict | None:
        return project_map.get(slug)

    async def fake_get_public_surface_ops_status(_session) -> dict:
        return {
            "last_public_refresh_at": "Mar 23, 2026 09:00 AM",
            "latest_public_run_status": "completed",
            "latest_wave_deploy_at": "",
        }

    monkeypatch.setattr(public_routes, "async_session", _fake_async_session)
    monkeypatch.setattr(public_routes, "get_public_profile", fake_get_public_profile)
    monkeypatch.setattr(public_routes, "list_public_projects", fake_list_public_projects)
    monkeypatch.setattr(public_routes, "list_public_faq", fake_list_public_faq)
    monkeypatch.setattr(public_routes, "get_public_answer_policy", fake_get_public_answer_policy)
    monkeypatch.setattr(public_routes, "get_public_project", fake_get_public_project)
    monkeypatch.setattr(public_routes, "get_public_surface_ops_status", fake_get_public_surface_ops_status)

    yield TestClient(app)


@pytest.mark.parametrize(
    ("path", "heading"),
    [
        ("/", "The site can explain me in my own language."),
        ("/about", "The resume, with the actual person still intact."),
        ("/work", "Flagship case studies first. Smaller proof after that."),
        ("/brain", "Ask the public-safe version of my brain."),
    ],
)
def test_public_overhaul_pages_render(client: TestClient, path: str, heading: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert heading in response.text


@pytest.mark.parametrize(
    "slug",
    [
        "dusrabheja",
        "datagenie",
        "balkan-barbershop-website",
        "kaffa-espresso-bar-website",
    ],
)
def test_flagship_case_study_routes_render(client: TestClient, slug: str) -> None:
    project = next(item for item in _sample_projects() if item["slug"] == slug)

    for path in (f"/projects/{slug}", f"/work/{slug}"):
        response = client.get(path)
        assert response.status_code == 200
        assert project["title"] in response.text
        assert "Role and Ownership" in response.text
        assert "Constraints" in response.text
        assert "Outcomes" in response.text
        assert "Architecture narrative" in response.text
        assert "Evidence Appendix" in response.text


def test_case_study_routes_do_not_render_raw_dump_markdown(client: TestClient) -> None:
    response = client.get("/projects/dusrabheja")

    assert response.status_code == 200
    assert "Resume (3 bullets)" not in response.text
    assert "LinkedIn (longer form)" not in response.text
    assert "All approved photos" not in client.get("/about").text


def test_about_experience_cards_prefer_bullets_without_duplicate_summary(client: TestClient) -> None:
    response = client.get("/about")

    assert response.status_code == 200
    assert response.text.count("Advertising platform team, cross-collaboration expansion team") == 1


def test_about_page_does_not_reuse_same_photo_src(client: TestClient) -> None:
    response = client.get("/about")

    assert response.status_code == 200
    sources = re.findall(r'<img src="([^"]+)" alt=', response.text)
    assert len(sources) == len(set(sources))


def test_public_health_reports_chat_and_refresh_state(client: TestClient) -> None:
    response = client.get("/api/public/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["chat_enabled"] is True
    assert "last_public_refresh_at" in payload
