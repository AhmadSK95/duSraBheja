from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from src.services import identity


def test_normalize_alias_compacts_paths_and_symbols() -> None:
    assert identity.normalize_alias("Desktop/duSraBheja") == "desktop-dusrabheja"


def test_alias_candidates_include_leaf_names_and_repo_forms() -> None:
    aliases = identity.alias_candidates(
        "/Users/ahmad/Desktop/duSraBheja",
        "git@github.com:moe/duSraBheja.git",
        "moe/duSraBheja",
    )

    lowered = {item.lower() for item in aliases}
    assert "dusrabheja" in lowered
    assert "git@github.com:moe/dusrabheja.git" in lowered


def test_resolve_project_does_not_create_generic_project_names(monkeypatch) -> None:
    async def fake_resolve_project_alias(session, alias):
        return None

    async def fake_find_notes_by_title(session, title, category=None):
        return []

    async def fake_get_or_create_project_note(session, title):
        raise AssertionError("generic project names should not be auto-created")

    monkeypatch.setattr(identity.store, "resolve_project_alias", fake_resolve_project_alias)
    monkeypatch.setattr(identity.store, "find_notes_by_title", fake_find_notes_by_title)
    monkeypatch.setattr(identity.store, "get_or_create_project_note", fake_get_or_create_project_note)

    result = asyncio.run(
        identity.resolve_project(
            object(),
            project_hint="frontend",
            create_if_missing=True,
        )
    )

    assert result is None


def test_infer_project_from_text_prefers_specific_alias_over_generic(monkeypatch) -> None:
    project = SimpleNamespace(id=uuid4(), title="duSraBheja")

    async def fake_list_active_project_aliases(session, limit=100):
        return ["frontend", "duSraBheja"]

    async def fake_resolve_project(session, **kwargs):
        if kwargs.get("project_hint") == "duSraBheja":
            return project
        return None

    monkeypatch.setattr(identity.store, "list_active_project_aliases", fake_list_active_project_aliases)
    monkeypatch.setattr(identity, "resolve_project", fake_resolve_project)

    result = asyncio.run(
        identity.infer_project_from_text(
            object(),
            "Need to push duSraBheja today; frontend is just a generic area mention.",
        )
    )

    assert result is project
