from __future__ import annotations

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
    assert narrative["personal_signals"]["family"] == ["Annie", "Oscar", "Iris"]


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

    monkeypatch.setattr(public_routes, "async_session", _fake_async_session)
    monkeypatch.setattr(public_routes, "get_public_profile", fake_get_public_profile)
    monkeypatch.setattr(public_routes, "list_public_projects", fake_list_public_projects)
    monkeypatch.setattr(public_routes, "list_public_faq", fake_list_public_faq)
    monkeypatch.setattr(public_routes, "get_public_answer_policy", fake_get_public_answer_policy)
    monkeypatch.setattr(public_routes, "get_public_project", fake_get_public_project)

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
