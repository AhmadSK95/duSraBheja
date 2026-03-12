from __future__ import annotations

import pytest

from src.lib import store


class _FakeMappingsResult:
    def all(self):
        return []


class _FakeResult:
    def mappings(self):
        return _FakeMappingsResult()


class _FakeSession:
    def __init__(self) -> None:
        self.sql = None
        self.params = None

    async def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return _FakeResult()


@pytest.mark.asyncio
async def test_vector_search_uses_cast_for_embedding_parameter() -> None:
    session = _FakeSession()

    await store.vector_search(session, [0.1, 0.2, 0.3], limit=5, min_similarity=0.4)

    assert "CAST(:embedding AS vector)" in session.sql.text
    assert ":embedding::vector" not in session.sql.text
    assert session.params["embedding"] == "[0.1,0.2,0.3]"
