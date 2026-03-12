from __future__ import annotations

from src.services.identity import alias_candidates, normalize_alias


def test_normalize_alias_compacts_paths_and_symbols() -> None:
    assert normalize_alias("Desktop/duSraBheja") == "desktop-dusrabheja"


def test_alias_candidates_include_leaf_names_and_repo_forms() -> None:
    aliases = alias_candidates(
        "/Users/ahmad/Desktop/duSraBheja",
        "git@github.com:moe/duSraBheja.git",
        "moe/duSraBheja",
    )

    lowered = {item.lower() for item in aliases}
    assert "dusrabheja" in lowered
    assert "git@github.com:moe/dusrabheja.git" in lowered
