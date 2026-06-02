"""Vault service — thin persistence layer over ``src/lib/vault_crypto.py``.

Wraps the crypto primitives with SQLAlchemy ORM access. Higher-level code
(route handlers, MCP tools, the worker pipeline) goes through this module
so the crypto layer stays infrastructure-free.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import vault_crypto
from src.lib.vault_crypto import UnlockedVault, VaultMaterial as VaultMaterialDC
from src.models import VaultMaterial, VaultUnlockSession

log = logging.getLogger("brain-vault")

# Minimum passphrase length when the owner brings their own. Diceware-
# generated phrases skip this since they're random; the route layer
# enforces "either diceware OR ≥ this length" semantics.
MIN_OWN_PASSPHRASE_LEN = 16


class VaultAlreadyInitializedError(RuntimeError):
    """Raised when init is attempted but a singleton row already exists."""


class VaultNotInitializedError(RuntimeError):
    """Raised when an operation requires an initialized vault but none exists."""


@dataclass(frozen=True)
class VaultStatus:
    initialized: bool
    public_key_b64: str | None  # short urlsafe-b64 fingerprint, owner-facing
    version: int | None


# ── Read ──────────────────────────────────────────────────────────────────


async def is_vault_initialized(session: AsyncSession) -> bool:
    """True iff the singleton ``vault_material`` row exists."""
    result = await session.execute(select(VaultMaterial.id).limit(1))
    return result.scalar_one_or_none() is not None


async def get_vault_status(session: AsyncSession) -> VaultStatus:
    """Owner-facing status for the dashboard. Never includes anything that
    could weaken security if leaked — public key is public, version is
    informational.
    """
    row = (await session.execute(select(VaultMaterial).limit(1))).scalar_one_or_none()
    if row is None:
        return VaultStatus(initialized=False, public_key_b64=None, version=None)
    return VaultStatus(
        initialized=True,
        public_key_b64=_short_public_key_fingerprint(row.vault_public_key),
        version=row.version,
    )


async def get_vault_material_record(session: AsyncSession) -> VaultMaterial:
    """Returns the ORM row, or raises if uninitialized.

    Routes that need to *unlock* read the row + pass its fields into
    ``vault_crypto.unlock()``. We keep the dataclass/ORM split deliberate:
    the crypto layer operates on a plain dataclass so it stays testable
    without a DB.
    """
    row = (await session.execute(select(VaultMaterial).limit(1))).scalar_one_or_none()
    if row is None:
        raise VaultNotInitializedError("Vault is not initialized")
    return row


def row_to_dataclass(row: VaultMaterial) -> VaultMaterialDC:
    """Convert an ORM ``VaultMaterial`` row to the crypto-layer dataclass."""
    return VaultMaterialDC(
        salt=bytes(row.salt),
        kdf_params=dict(row.kdf_params or {}),
        vault_public_key=bytes(row.vault_public_key),
        encrypted_vault_private_key=bytes(row.encrypted_vault_private_key),
        private_key_nonce=bytes(row.private_key_nonce),
        version=int(row.version),
    )


# ── Write ─────────────────────────────────────────────────────────────────


async def initialize_vault(session: AsyncSession, passphrase: str) -> VaultStatus:
    """One-time setup. Generates the keypair, derives the KEK from the
    passphrase, persists the singleton row.

    Idempotent only in the sense that a second call raises rather than
    silently overwriting — the UNIQUE(singleton) constraint at the DB
    level is the ultimate guard, this check produces a clean error first.

    The passphrase is **not** stored anywhere. It's used inside this
    function to derive the KEK, and the call frame discards both as soon
    as the encrypted private key is materialized.
    """
    if not passphrase:
        raise ValueError("Passphrase required for vault setup")

    if await is_vault_initialized(session):
        raise VaultAlreadyInitializedError(
            "Vault is already initialized; use change_passphrase to rotate."
        )

    material_dc = vault_crypto.initialize_vault(passphrase)

    row = VaultMaterial(
        salt=material_dc.salt,
        kdf_params=dict(material_dc.kdf_params),
        vault_public_key=material_dc.vault_public_key,
        encrypted_vault_private_key=material_dc.encrypted_vault_private_key,
        private_key_nonce=material_dc.private_key_nonce,
        version=material_dc.version,
    )
    session.add(row)
    await session.flush()
    log.info("Vault initialized (version=%s)", material_dc.version)

    return VaultStatus(
        initialized=True,
        public_key_b64=_short_public_key_fingerprint(material_dc.vault_public_key),
        version=material_dc.version,
    )


# ── Helpers ───────────────────────────────────────────────────────────────


def _short_public_key_fingerprint(public_key: bytes) -> str:
    """Owner-facing short fingerprint of the vault public key.

    Use case: the setup confirmation page can show "Vault key ID: 7Hk2…vQ"
    so the owner can sanity-check it's the same vault on re-unlock. It's
    just the first 8 chars of the urlsafe-b64 encoding of the public key.
    """
    import base64

    encoded = base64.urlsafe_b64encode(bytes(public_key)).decode("ascii")
    return encoded[:8]


# ── Unlock state (process-local) ──────────────────────────────────────────
#
# Process-local because the unwrapped private key never leaves RAM. When the
# container restarts, every unlock is dropped — the owner re-enters the
# passphrase. The DB-side `VaultUnlockSession` row records intent + audit
# info; it doesn't store the key.
#
# Multi-worker note (revisited 2026-06-02): putting the unwrapped key in a
# shared store (Redis) would require a wrapping key on the droplet, which
# collapses the threat model back to "anything on the droplet decrypts the
# vault." So we don't. Multi-worker, when it happens, is solved with
# sticky-session config at the reverse proxy — not with a vault rewrite.

UNLOCK_TTL_HOURS_DEFAULT = 8

_UNLOCKED_VAULTS: dict[str, UnlockedVault] = {}
_UNLOCK_EXPIRY: dict[str, datetime] = {}
_UNLOCK_LOCK = RLock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_unlocked_vault(unlock_session_id: str | None) -> UnlockedVault | None:
    """Return the unwrapped vault for this session, or None if locked /
    expired / unknown. Auto-evicts expired entries.
    """
    if not unlock_session_id:
        return None
    with _UNLOCK_LOCK:
        expiry = _UNLOCK_EXPIRY.get(unlock_session_id)
        if expiry is None:
            return None
        if expiry <= _utcnow():
            _UNLOCKED_VAULTS.pop(unlock_session_id, None)
            _UNLOCK_EXPIRY.pop(unlock_session_id, None)
            return None
        return _UNLOCKED_VAULTS.get(unlock_session_id)


def is_session_unlocked(unlock_session_id: str | None) -> bool:
    return get_unlocked_vault(unlock_session_id) is not None


def _wipe_session_state(unlock_session_id: str) -> bool:
    """Drop a session from the in-memory dicts. Returns True if anything
    was removed.
    """
    with _UNLOCK_LOCK:
        had = unlock_session_id in _UNLOCKED_VAULTS or unlock_session_id in _UNLOCK_EXPIRY
        _UNLOCKED_VAULTS.pop(unlock_session_id, None)
        _UNLOCK_EXPIRY.pop(unlock_session_id, None)
        return had


def _reset_all_sessions_for_tests() -> None:
    """Test-only helper. Clears every in-memory unlock. Not used in prod."""
    with _UNLOCK_LOCK:
        _UNLOCKED_VAULTS.clear()
        _UNLOCK_EXPIRY.clear()


async def unlock_vault_for_session(
    session: AsyncSession,
    *,
    unlock_session_id: str,
    passphrase: str,
    ip: str | None = None,
    device_label: str | None = None,
    device_fingerprint: str = "",
    ttl_hours: int = UNLOCK_TTL_HOURS_DEFAULT,
) -> VaultUnlockSession:
    """Verify the passphrase, decrypt the vault private key into RAM, and
    record an unlock session row.

    Raises:
        ValueError: if ``unlock_session_id`` or ``passphrase`` is empty.
        VaultNotInitializedError: if no vault exists.
        vault_crypto.VaultPassphraseError: if the passphrase is wrong.
    """
    if not unlock_session_id:
        raise ValueError("unlock_session_id required")
    if not passphrase:
        raise vault_crypto.VaultPassphraseError("Passphrase required")

    material_row = await get_vault_material_record(session)
    material_dc = row_to_dataclass(material_row)
    unlocked = vault_crypto.unlock(passphrase, material_dc)  # raises on bad passphrase

    now = _utcnow()
    expires_at = now + timedelta(hours=ttl_hours)

    with _UNLOCK_LOCK:
        _UNLOCKED_VAULTS[unlock_session_id] = unlocked
        _UNLOCK_EXPIRY[unlock_session_id] = expires_at

    existing = (
        await session.execute(
            select(VaultUnlockSession).where(
                VaultUnlockSession.session_id == unlock_session_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.unlocked_at = now
        existing.expires_at = expires_at
        existing.last_active_at = now
        if ip is not None:
            existing.ip = ip
        if device_label is not None:
            existing.device_label = device_label
        if device_fingerprint:
            existing.device_fingerprint = device_fingerprint
        row = existing
    else:
        row = VaultUnlockSession(
            session_id=unlock_session_id,
            device_label=device_label,
            device_fingerprint=device_fingerprint or "unknown",
            ip=ip,
            unlocked_at=now,
            expires_at=expires_at,
            last_active_at=now,
        )
        session.add(row)
    await session.flush()
    log.info("vault unlocked for session %s (ttl=%dh)", unlock_session_id[:8], ttl_hours)
    return row


async def lock_vault_for_session(
    session: AsyncSession, *, unlock_session_id: str
) -> bool:
    """Wipe the in-memory unlock + delete the DB session row.

    Returns True if anything was actually locked (either layer had an
    entry). Safe to call on an already-locked session.
    """
    if not unlock_session_id:
        return False
    had_memory = _wipe_session_state(unlock_session_id)
    result = await session.execute(
        delete(VaultUnlockSession).where(VaultUnlockSession.session_id == unlock_session_id)
    )
    had_db = bool(getattr(result, "rowcount", 0) or 0)
    if had_memory or had_db:
        log.info("vault locked for session %s", unlock_session_id[:8])
    return had_memory or had_db


async def touch_unlock_session(
    session: AsyncSession, *, unlock_session_id: str
) -> None:
    """Update ``last_active_at`` on the unlock row. Cheap; call on routes
    that use the unlocked vault so the dashboard can surface
    "last active 12s ago" instead of just unlock time.
    """
    if not unlock_session_id:
        return
    await session.execute(
        VaultUnlockSession.__table__.update()
        .where(VaultUnlockSession.session_id == unlock_session_id)
        .values(last_active_at=_utcnow())
    )
