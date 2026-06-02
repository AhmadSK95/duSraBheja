"""Vault service — thin persistence layer over ``src/lib/vault_crypto.py``.

Wraps the crypto primitives with SQLAlchemy ORM access. Higher-level code
(route handlers, MCP tools, the worker pipeline) goes through this module
so the crypto layer stays infrastructure-free.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.lib import vault_crypto
from src.lib.vault_crypto import VaultMaterial as VaultMaterialDC  # disambiguate
from src.models import VaultMaterial

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
