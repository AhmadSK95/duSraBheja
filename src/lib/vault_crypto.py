"""Vault crypto primitives — asymmetric envelope encryption for secrets.

Design rationale: the worker pipeline ingests secrets 24/7, including when no
one is logged in. So encryption-at-rest cannot depend on the owner's
passphrase being available at ingest time. We use hybrid envelope encryption:

    - At vault setup, a long-lived X25519 keypair is generated.
    - `vault_public_key` is stored in plaintext on the droplet (it's public).
    - `vault_private_key` is encrypted at rest with a KEK derived from the
      owner's passphrase via Argon2id. The KEK never touches disk.
    - Ingest path uses `encrypt_for_vault(plaintext, vault_public_key)` —
      ECIES (X25519 + HKDF + AES-256-GCM). No unlock required.
    - Reveal path requires unlocking: re-derive KEK from passphrase → decrypt
      the vault private key into RAM → use it to decrypt envelopes.

The KEK is never persisted anywhere; the unlocked private key lives only in
process memory tied to the owner's session. A droplet compromise gets the
ciphertext + public key + (encrypted) private key — useless without the
passphrase that exists only in the owner's head.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Argon2id parameters tuned for ~0.5–1s on a typical server CPU. Bumping
# iterations or memory_cost is forward-compatible because params are stored
# alongside the salt in `VaultMaterial.kdf_params`.
DEFAULT_KDF_PARAMS: dict[str, int] = {
    "iterations": 3,
    "lanes": 4,
    "memory_cost_kib": 65536,  # 64 MiB
}

ENVELOPE_ALG = "X25519-HKDF-SHA256-AES256GCM"
HKDF_INFO = b"dusrabheja-vault-envelope-v1"


class VaultLockedError(RuntimeError):
    """Raised when a decryption is attempted without an unlocked vault."""


class VaultPassphraseError(RuntimeError):
    """Raised when the supplied passphrase does not unlock the vault."""


@dataclass(frozen=True)
class VaultMaterial:
    """At-rest material persisted in the DB. None of this is sensitive on
    its own — it requires the owner's passphrase to be useful.
    """

    salt: bytes
    kdf_params: dict[str, int]
    vault_public_key: bytes  # 32 bytes, X25519 raw
    encrypted_vault_private_key: bytes
    private_key_nonce: bytes
    version: int = 1


@dataclass
class UnlockedVault:
    """In-memory only. Never serialized, never logged, never persisted.

    Held by the API process for the duration of the owner's unlock session
    and wiped from memory on lock / logout / session expiry.
    """

    vault_public_key: bytes
    _private_key: x25519.X25519PrivateKey = field(repr=False)

    def public_key(self) -> bytes:
        return self.vault_public_key


# ── KEK derivation ─────────────────────────────────────────────────────────


def derive_kek(passphrase: str, salt: bytes, *, kdf_params: dict[str, int] | None = None) -> bytes:
    """Derive a 32-byte KEK from the passphrase + salt via Argon2id.

    Parameters live in `kdf_params` so a future deploy can ratchet them up
    without breaking older vaults — each vault stores the params it was set
    up with.
    """
    params = {**DEFAULT_KDF_PARAMS, **(kdf_params or {})}
    return Argon2id(
        salt=salt,
        length=32,
        iterations=params["iterations"],
        lanes=params["lanes"],
        memory_cost=params["memory_cost_kib"],
    ).derive(passphrase.encode("utf-8"))


# ── Setup, unlock, lock ────────────────────────────────────────────────────


def initialize_vault(passphrase: str, *, kdf_params: dict[str, int] | None = None) -> VaultMaterial:
    """One-time setup. Generates the X25519 keypair, encrypts the private key
    with a KEK derived from the passphrase, returns the at-rest material.

    Caller is responsible for persisting `VaultMaterial` and prompting the
    owner to back the passphrase up — there's no recovery if it's lost.
    """
    if not passphrase:
        raise ValueError("Passphrase required for vault initialization")

    salt = os.urandom(32)
    kek = derive_kek(passphrase, salt, kdf_params=kdf_params)

    vault_private = x25519.X25519PrivateKey.generate()
    vault_private_raw = vault_private.private_bytes_raw()
    vault_public_raw = vault_private.public_key().public_bytes_raw()

    # Encrypt the private key with the KEK. Associated data binds it to the
    # version and public key so cross-vault swap attacks fail at decrypt.
    nonce = os.urandom(12)
    aad = _setup_aad(vault_public_raw, version=1)
    encrypted_private = AESGCM(kek).encrypt(nonce, vault_private_raw, aad)

    return VaultMaterial(
        salt=salt,
        kdf_params={**DEFAULT_KDF_PARAMS, **(kdf_params or {})},
        vault_public_key=vault_public_raw,
        encrypted_vault_private_key=encrypted_private,
        private_key_nonce=nonce,
        version=1,
    )


def unlock(passphrase: str, material: VaultMaterial) -> UnlockedVault:
    """Re-derive the KEK and decrypt the vault private key into RAM.

    Raises:
        VaultPassphraseError: if the passphrase doesn't unlock the vault
            (covers both wrong passphrase and tampered material).
    """
    if not passphrase:
        raise VaultPassphraseError("Passphrase required")

    kek = derive_kek(passphrase, material.salt, kdf_params=material.kdf_params)
    aad = _setup_aad(material.vault_public_key, version=material.version)

    try:
        private_raw = AESGCM(kek).decrypt(
            material.private_key_nonce,
            material.encrypted_vault_private_key,
            aad,
        )
    except InvalidTag as exc:
        raise VaultPassphraseError("Passphrase did not unlock the vault") from exc
    finally:
        # Best-effort wipe of the KEK from this stack frame; the cryptography
        # lib may keep transient copies internally that we can't reach.
        kek = b"\x00" * len(kek)

    return UnlockedVault(
        vault_public_key=material.vault_public_key,
        _private_key=x25519.X25519PrivateKey.from_private_bytes(private_raw),
    )


def change_passphrase(
    old_passphrase: str,
    new_passphrase: str,
    material: VaultMaterial,
    *,
    new_kdf_params: dict[str, int] | None = None,
) -> VaultMaterial:
    """Re-wrap the vault private key with a new KEK. The keypair itself
    doesn't change, so existing envelopes remain decryptable.
    """
    unlocked = unlock(old_passphrase, material)
    private_raw = unlocked._private_key.private_bytes_raw()

    salt = os.urandom(32)
    kek = derive_kek(new_passphrase, salt, kdf_params=new_kdf_params)
    nonce = os.urandom(12)
    aad = _setup_aad(material.vault_public_key, version=material.version)
    encrypted_private = AESGCM(kek).encrypt(nonce, private_raw, aad)

    # Wipe ephemeral copies.
    private_raw = b"\x00" * len(private_raw)
    kek = b"\x00" * len(kek)

    return VaultMaterial(
        salt=salt,
        kdf_params={**DEFAULT_KDF_PARAMS, **(new_kdf_params or {})},
        vault_public_key=material.vault_public_key,
        encrypted_vault_private_key=encrypted_private,
        private_key_nonce=nonce,
        version=material.version,
    )


# ── Envelope encrypt / decrypt ─────────────────────────────────────────────


def encrypt_for_vault(plaintext: bytes, vault_public_key: bytes, *, aad: bytes = b"") -> dict[str, Any]:
    """Encrypt `plaintext` against the vault's public key. Callable without
    unlock — the worker uses this on ingest.

    ECIES: generate an ephemeral X25519 keypair, ECDH against the vault
    public key, HKDF-derive a symmetric key, AES-256-GCM the plaintext.
    The ephemeral private key is discarded after derivation; the receiver
    re-derives the same symmetric key from the ephemeral public key + the
    vault private key.

    Wire format is dict-of-base64 so it round-trips through JSON columns.
    """
    if len(vault_public_key) != 32:
        raise ValueError("vault_public_key must be 32 raw X25519 bytes")

    eph_private = x25519.X25519PrivateKey.generate()
    eph_public_raw = eph_private.public_key().public_bytes_raw()
    receiver_public = x25519.X25519PublicKey.from_public_bytes(vault_public_key)
    shared = eph_private.exchange(receiver_public)

    symmetric = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=eph_public_raw + vault_public_key,
        info=HKDF_INFO,
    ).derive(shared)

    nonce = os.urandom(12)
    ciphertext = AESGCM(symmetric).encrypt(nonce, plaintext, aad or None)

    # Wipe the symmetric key from this frame.
    symmetric = b"\x00" * len(symmetric)

    return {
        "alg": ENVELOPE_ALG,
        "ephemeral_pub": _b64(eph_public_raw),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
        "aad_b64": _b64(aad) if aad else "",
    }


def decrypt_from_vault(envelope: dict[str, Any], unlocked: UnlockedVault) -> bytes:
    """Decrypt an envelope produced by `encrypt_for_vault`. Requires an
    `UnlockedVault` — caller must verify the owner's session is unlocked.
    """
    if unlocked is None:
        raise VaultLockedError("Vault is locked")
    if envelope.get("alg") != ENVELOPE_ALG:
        raise ValueError(f"Unknown envelope alg: {envelope.get('alg')!r}")

    eph_public_raw = _b64d(envelope["ephemeral_pub"])
    nonce = _b64d(envelope["nonce"])
    ciphertext = _b64d(envelope["ciphertext"])
    aad = _b64d(envelope["aad_b64"]) if envelope.get("aad_b64") else None

    eph_public = x25519.X25519PublicKey.from_public_bytes(eph_public_raw)
    shared = unlocked._private_key.exchange(eph_public)

    symmetric = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=eph_public_raw + unlocked.vault_public_key,
        info=HKDF_INFO,
    ).derive(shared)

    try:
        plaintext = AESGCM(symmetric).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise ValueError("Envelope failed authentication (tampered or wrong vault)") from exc
    finally:
        symmetric = b"\x00" * len(symmetric)

    return plaintext


# ── Internal helpers ───────────────────────────────────────────────────────


def _setup_aad(vault_public_key: bytes, *, version: int) -> bytes:
    """Bind the encrypted private key to its public key + version so that
    an attacker who can swap the at-rest material can't graft a private
    key from one vault into another.
    """
    return f"vault-setup-v{version}".encode("ascii") + vault_public_key


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))
