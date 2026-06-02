"""Tests for src/services/vault.py.

These cover the persistence wiring around the crypto layer. The crypto
itself is exercised in tests/test_vault_crypto.py; here we just verify
the service correctly persists, queries, and round-trips the ORM row.

We don't need a live Postgres for these — a tiny in-memory fake session
that records `add()` and resolves `execute(select(...))` against a dict
is enough. The DB-level UNIQUE constraint on `singleton` is still
trusted; we test the service-level "already initialized" guard which
runs first.
"""

from __future__ import annotations

import pytest

from src.lib import vault_crypto
from src.models import VaultMaterial
from src.services import vault as vault_service


# ── In-memory fake session ────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """Minimal stand-in for AsyncSession. Holds at most one VaultMaterial
    row. Supports the two query shapes the service actually issues:
    ``select(VaultMaterial.id).limit(1)`` and ``select(VaultMaterial).limit(1)``.
    """

    def __init__(self) -> None:
        self.row: VaultMaterial | None = None
        self.flushed = False

    async def execute(self, _stmt):
        # Service uses select(VaultMaterial[.id]).limit(1) — for the purposes
        # of these tests we ignore the statement structure and just return
        # whatever's in self.row.
        return _FakeResult(self.row)

    def add(self, instance) -> None:
        assert isinstance(instance, VaultMaterial)
        self.row = instance

    async def flush(self) -> None:
        self.flushed = True


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """Replace production KDF params with weak ones so the suite stays fast.
    Real defaults are tested in tests/test_vault_crypto.py.
    """
    fast = {"iterations": 1, "lanes": 2, "memory_cost_kib": 8192}
    monkeypatch.setattr(vault_crypto, "DEFAULT_KDF_PARAMS", fast)


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


PASSPHRASE = "correct-horse-battery-staple-mango-velvet"


# ── Status / read ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_vault_initialized_false_when_empty(session: FakeSession) -> None:
    assert await vault_service.is_vault_initialized(session) is False


@pytest.mark.asyncio
async def test_get_vault_status_empty(session: FakeSession) -> None:
    status = await vault_service.get_vault_status(session)
    assert status.initialized is False
    assert status.public_key_b64 is None
    assert status.version is None


@pytest.mark.asyncio
async def test_get_vault_material_record_raises_when_empty(session: FakeSession) -> None:
    with pytest.raises(vault_service.VaultNotInitializedError):
        await vault_service.get_vault_material_record(session)


# ── Setup ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_vault_persists_row(session: FakeSession) -> None:
    status = await vault_service.initialize_vault(session, PASSPHRASE)

    assert status.initialized is True
    assert status.public_key_b64 is not None and len(status.public_key_b64) == 8
    assert status.version == 1

    assert session.row is not None
    row = session.row
    assert len(bytes(row.salt)) == 32
    assert len(bytes(row.vault_public_key)) == 32
    assert len(bytes(row.private_key_nonce)) == 12
    assert dict(row.kdf_params).get("iterations") == 1  # fast-kdf fixture
    assert row.version == 1
    assert session.flushed is True


@pytest.mark.asyncio
async def test_initialize_vault_rejects_empty_passphrase(session: FakeSession) -> None:
    with pytest.raises(ValueError):
        await vault_service.initialize_vault(session, "")
    assert session.row is None


@pytest.mark.asyncio
async def test_second_init_raises_already_initialized(session: FakeSession) -> None:
    await vault_service.initialize_vault(session, PASSPHRASE)
    with pytest.raises(vault_service.VaultAlreadyInitializedError):
        await vault_service.initialize_vault(session, "another-passphrase")


# ── Round-trip through the dataclass bridge ───────────────────────────────


@pytest.mark.asyncio
async def test_row_to_dataclass_round_trip_unlocks(session: FakeSession) -> None:
    """Persistence + retrieval + crypto unlock must still produce a working
    UnlockedVault — i.e. the dataclass we get from the ORM is identical
    enough to the one vault_crypto.initialize_vault produced to unlock.
    """
    await vault_service.initialize_vault(session, PASSPHRASE)
    row = await vault_service.get_vault_material_record(session)

    material_dc = vault_service.row_to_dataclass(row)
    unlocked = vault_crypto.unlock(PASSPHRASE, material_dc)
    assert unlocked.vault_public_key == row.vault_public_key


@pytest.mark.asyncio
async def test_status_public_key_fingerprint_is_stable(session: FakeSession) -> None:
    """The fingerprint on status should be derivable from just the public
    key — same key, same fingerprint, across calls.
    """
    await vault_service.initialize_vault(session, PASSPHRASE)
    status_a = await vault_service.get_vault_status(session)
    status_b = await vault_service.get_vault_status(session)
    assert status_a.public_key_b64 == status_b.public_key_b64


# ── Status checks ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_vault_initialized_true_after_init(session: FakeSession) -> None:
    await vault_service.initialize_vault(session, PASSPHRASE)
    assert await vault_service.is_vault_initialized(session) is True


@pytest.mark.asyncio
async def test_get_vault_status_initialized(session: FakeSession) -> None:
    await vault_service.initialize_vault(session, PASSPHRASE)
    status = await vault_service.get_vault_status(session)
    assert status.initialized is True
    assert status.version == 1
    assert status.public_key_b64 is not None
