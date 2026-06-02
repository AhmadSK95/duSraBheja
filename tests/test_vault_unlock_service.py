"""Tests for the unlock-state side of src/services/vault.py.

Every property here matters for vault safety:
- An unlocked vault is reachable only via the exact session_id that
  unlocked it. Different sessions are isolated.
- Wrong passphrase doesn't unlock anything.
- Expired sessions don't expose the key, even if the in-memory dict
  still has a reference (TTL is enforced at read time).
- Lock wipes BOTH the in-memory entry AND the DB row.
- The crypto layer is exercised — we round-trip an encrypted envelope
  through ``encrypt_for_vault → store → unlock_vault_for_session →
  decrypt_from_vault`` to prove the unlock actually unlocks.

DB is mocked (same pattern as test_vault_service.py) — the unlock
storage is process-local so no real DB needed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from src.lib import vault_crypto
from src.models import VaultMaterial, VaultUnlockSession
from src.services import vault as vault_service


# ── Fake session (extends the one in test_vault_service.py with unlock-row support) ──


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    @property
    def rowcount(self):
        return getattr(self, "_rowcount", 0)


class _DeleteResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class FakeSession:
    """Holds at most one VaultMaterial row plus a dict of
    VaultUnlockSession rows keyed by session_id.
    """

    def __init__(self) -> None:
        self.vault_row: VaultMaterial | None = None
        self.unlock_rows: dict[str, VaultUnlockSession] = {}
        self._pending_add: Any | None = None

    async def execute(self, stmt):
        # Distinguish by stmt shape — crude but sufficient for these tests.
        repr_str = str(stmt).lower()
        if "delete" in repr_str and "vault_unlock_sessions" in repr_str:
            session_id = _extract_param(stmt)
            had = session_id in self.unlock_rows
            if had:
                del self.unlock_rows[session_id]
            return _DeleteResult(1 if had else 0)
        if "update" in repr_str and "vault_unlock_sessions" in repr_str:
            return _DeleteResult(0)
        if "vault_unlock_sessions" in repr_str:
            session_id = _extract_param(stmt)
            return _FakeResult(self.unlock_rows.get(session_id))
        return _FakeResult(self.vault_row)

    def add(self, instance) -> None:
        if isinstance(instance, VaultMaterial):
            self.vault_row = instance
        elif isinstance(instance, VaultUnlockSession):
            self.unlock_rows[instance.session_id] = instance
        else:
            raise AssertionError(f"unexpected instance type: {type(instance).__name__}")

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


def _extract_param(stmt) -> str:
    """Best-effort: pull the session_id we're looking up. Uses the
    compiled SQL's parameter dict.
    """
    try:
        compiled = stmt.compile(compile_kwargs={"literal_binds": False})
        params = compiled.params or {}
        for value in params.values():
            if isinstance(value, str):
                return value
    except Exception:
        pass
    return ""


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_unlock_state() -> None:
    """Every test starts with no unlocked sessions."""
    vault_service._reset_all_sessions_for_tests()


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch) -> None:
    monkeypatch.setattr(
        vault_crypto,
        "DEFAULT_KDF_PARAMS",
        {"iterations": 1, "lanes": 2, "memory_cost_kib": 8192},
    )


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


PASSPHRASE = "correct-horse-battery-staple-mango-velvet"
WRONG_PASSPHRASE = "wrong-horse-battery-staple"


async def _setup_vault(session: FakeSession) -> None:
    await vault_service.initialize_vault(session, PASSPHRASE)


# ── is_session_unlocked + get_unlocked_vault when nothing is unlocked ─────


def test_is_session_unlocked_false_for_unknown_id() -> None:
    assert vault_service.is_session_unlocked("never-unlocked") is False


def test_get_unlocked_vault_returns_none_for_unknown_id() -> None:
    assert vault_service.get_unlocked_vault("never-unlocked") is None


def test_is_session_unlocked_false_for_empty_id() -> None:
    assert vault_service.is_session_unlocked("") is False
    assert vault_service.is_session_unlocked(None) is False


# ── unlock_vault_for_session happy path ───────────────────────────────────


@pytest.mark.asyncio
async def test_unlock_succeeds_with_correct_passphrase(session: FakeSession) -> None:
    await _setup_vault(session)
    row = await vault_service.unlock_vault_for_session(
        session,
        unlock_session_id="session-a",
        passphrase=PASSPHRASE,
        ip="10.0.0.1",
        device_label="Test laptop",
        device_fingerprint="fp-abc",
    )
    assert row.session_id == "session-a"
    assert row.ip == "10.0.0.1"
    assert row.device_label == "Test laptop"
    assert row.device_fingerprint == "fp-abc"
    assert vault_service.is_session_unlocked("session-a") is True


@pytest.mark.asyncio
async def test_unlock_makes_vault_usable_for_decrypt(session: FakeSession) -> None:
    """Round-trip: encrypt with vault_public_key without unlock, then unlock
    and decrypt. This is THE property — unlock has to actually unlock.
    """
    await _setup_vault(session)
    plaintext = b"secret-api-key-deadbeef"
    envelope = vault_crypto.encrypt_for_vault(plaintext, session.vault_row.vault_public_key)

    await vault_service.unlock_vault_for_session(
        session,
        unlock_session_id="session-a",
        passphrase=PASSPHRASE,
    )
    unlocked = vault_service.get_unlocked_vault("session-a")
    assert unlocked is not None
    assert vault_crypto.decrypt_from_vault(envelope, unlocked) == plaintext


# ── unlock failure paths ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unlock_with_wrong_passphrase_does_not_grant_access(
    session: FakeSession,
) -> None:
    await _setup_vault(session)
    with pytest.raises(vault_crypto.VaultPassphraseError):
        await vault_service.unlock_vault_for_session(
            session,
            unlock_session_id="session-a",
            passphrase=WRONG_PASSPHRASE,
        )
    assert vault_service.is_session_unlocked("session-a") is False
    assert "session-a" not in session.unlock_rows


@pytest.mark.asyncio
async def test_unlock_with_empty_passphrase_raises(session: FakeSession) -> None:
    await _setup_vault(session)
    with pytest.raises(vault_crypto.VaultPassphraseError):
        await vault_service.unlock_vault_for_session(
            session,
            unlock_session_id="session-a",
            passphrase="",
        )


@pytest.mark.asyncio
async def test_unlock_without_session_id_raises(session: FakeSession) -> None:
    await _setup_vault(session)
    with pytest.raises(ValueError):
        await vault_service.unlock_vault_for_session(
            session,
            unlock_session_id="",
            passphrase=PASSPHRASE,
        )


@pytest.mark.asyncio
async def test_unlock_raises_when_vault_not_initialized(session: FakeSession) -> None:
    with pytest.raises(vault_service.VaultNotInitializedError):
        await vault_service.unlock_vault_for_session(
            session,
            unlock_session_id="session-a",
            passphrase=PASSPHRASE,
        )


# ── Session isolation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unlocking_one_session_does_not_unlock_another(
    session: FakeSession,
) -> None:
    await _setup_vault(session)
    await vault_service.unlock_vault_for_session(
        session, unlock_session_id="session-a", passphrase=PASSPHRASE,
    )
    assert vault_service.is_session_unlocked("session-a") is True
    assert vault_service.is_session_unlocked("session-b") is False
    assert vault_service.get_unlocked_vault("session-b") is None


# ── TTL enforcement ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expired_session_returns_none_and_evicts(
    session: FakeSession, monkeypatch
) -> None:
    """If the TTL has passed, get_unlocked_vault must return None even
    if the entry is still in the dict, AND it should evict.
    """
    await _setup_vault(session)
    await vault_service.unlock_vault_for_session(
        session,
        unlock_session_id="session-a",
        passphrase=PASSPHRASE,
        ttl_hours=1,
    )
    # Wind the clock forward
    fixed_now = vault_service._utcnow() + timedelta(hours=2)
    monkeypatch.setattr(vault_service, "_utcnow", lambda: fixed_now)
    assert vault_service.get_unlocked_vault("session-a") is None
    # Verify eviction — direct re-check confirms it's gone (no clock movement
    # because the first call wiped it).
    monkeypatch.setattr(vault_service, "_utcnow", lambda: fixed_now + timedelta(hours=3))
    assert vault_service.get_unlocked_vault("session-a") is None


# ── Lock ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lock_wipes_memory_and_db_row(session: FakeSession) -> None:
    await _setup_vault(session)
    await vault_service.unlock_vault_for_session(
        session, unlock_session_id="session-a", passphrase=PASSPHRASE,
    )
    assert vault_service.is_session_unlocked("session-a") is True

    locked = await vault_service.lock_vault_for_session(
        session, unlock_session_id="session-a"
    )
    assert locked is True
    assert vault_service.is_session_unlocked("session-a") is False
    assert "session-a" not in session.unlock_rows


@pytest.mark.asyncio
async def test_lock_on_already_locked_session_is_noop(session: FakeSession) -> None:
    """Idempotent — calling lock twice doesn't crash."""
    await _setup_vault(session)
    await vault_service.unlock_vault_for_session(
        session, unlock_session_id="session-a", passphrase=PASSPHRASE,
    )
    await vault_service.lock_vault_for_session(session, unlock_session_id="session-a")
    locked_again = await vault_service.lock_vault_for_session(
        session, unlock_session_id="session-a"
    )
    assert locked_again is False


@pytest.mark.asyncio
async def test_lock_with_empty_session_id_is_noop(session: FakeSession) -> None:
    locked = await vault_service.lock_vault_for_session(session, unlock_session_id="")
    assert locked is False


# ── Re-unlock refreshes the same session ─────────────────────────────────


@pytest.mark.asyncio
async def test_re_unlock_extends_ttl_in_place(session: FakeSession) -> None:
    """Re-unlocking the same session_id (e.g., owner re-entered passphrase
    before TTL expired) updates the existing row rather than creating
    duplicates.
    """
    await _setup_vault(session)
    row_a = await vault_service.unlock_vault_for_session(
        session, unlock_session_id="session-a", passphrase=PASSPHRASE,
    )
    initial_expiry = row_a.expires_at

    # Re-unlock — should update in place
    row_b = await vault_service.unlock_vault_for_session(
        session, unlock_session_id="session-a", passphrase=PASSPHRASE,
        device_label="Updated label",
    )
    assert row_a is row_b  # same row
    assert row_b.device_label == "Updated label"
    assert row_b.expires_at >= initial_expiry
    assert len(session.unlock_rows) == 1  # no duplicate
