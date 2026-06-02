"""Tests for src/lib/vault_crypto.py — the vault's foundational primitives.

Every property this file asserts is something a vault user is relying on:
- A bad passphrase doesn't grant access, ever.
- Ingest-time encryption works without unlock; reveal requires unlock.
- Tampering with ciphertext is detected.
- Rotating the passphrase preserves access to existing envelopes.
- Two encrypts of the same plaintext produce different ciphertexts.

If any of these break, the vault is compromised. Don't relax them.
"""

from __future__ import annotations

import pytest

from src.lib.vault_crypto import (
    DEFAULT_KDF_PARAMS,
    UnlockedVault,
    VaultLockedError,
    VaultPassphraseError,
    change_passphrase,
    decrypt_from_vault,
    derive_kek,
    encrypt_for_vault,
    initialize_vault,
    unlock,
)

# Use much smaller KDF cost in tests so the suite finishes in <5s instead
# of taking ~30s. We're not measuring KDF strength here; the production
# defaults are what's deployed.
TEST_KDF_PARAMS = {
    "iterations": 1,
    "lanes": 2,
    "memory_cost_kib": 8192,
}

PASSPHRASE = "correct-horse-battery-staple-mango-velvet"
WRONG_PASSPHRASE = "incorrect-horse-battery-staple-mango"


# ── Setup ─────────────────────────────────────────────────────────────────


def test_initialize_vault_produces_consistent_material() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    assert len(material.salt) == 32
    assert len(material.vault_public_key) == 32
    assert len(material.private_key_nonce) == 12
    assert material.version == 1
    assert material.kdf_params["iterations"] == TEST_KDF_PARAMS["iterations"]


def test_initialize_vault_rejects_empty_passphrase() -> None:
    with pytest.raises(ValueError):
        initialize_vault("", kdf_params=TEST_KDF_PARAMS)


def test_two_setups_produce_different_keypairs() -> None:
    a = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    b = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    assert a.vault_public_key != b.vault_public_key
    assert a.salt != b.salt
    assert a.encrypted_vault_private_key != b.encrypted_vault_private_key


# ── Unlock ────────────────────────────────────────────────────────────────


def test_unlock_with_correct_passphrase_succeeds() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    unlocked = unlock(PASSPHRASE, material)
    assert isinstance(unlocked, UnlockedVault)
    assert unlocked.vault_public_key == material.vault_public_key


def test_unlock_with_wrong_passphrase_raises() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    with pytest.raises(VaultPassphraseError):
        unlock(WRONG_PASSPHRASE, material)


def test_unlock_with_empty_passphrase_raises() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    with pytest.raises(VaultPassphraseError):
        unlock("", material)


def test_unlock_detects_tampered_private_key() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    # Flip one byte of the encrypted private key.
    tampered_bytes = bytearray(material.encrypted_vault_private_key)
    tampered_bytes[0] ^= 0x01
    tampered = type(material)(
        salt=material.salt,
        kdf_params=material.kdf_params,
        vault_public_key=material.vault_public_key,
        encrypted_vault_private_key=bytes(tampered_bytes),
        private_key_nonce=material.private_key_nonce,
        version=material.version,
    )
    with pytest.raises(VaultPassphraseError):
        unlock(PASSPHRASE, tampered)


def test_unlock_rejects_swapped_public_key() -> None:
    """An attacker who swaps `vault_public_key` while keeping the encrypted
    private key intact would otherwise be able to break the binding. The
    setup AAD prevents this.
    """
    material_a = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    material_b = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    swapped = type(material_a)(
        salt=material_a.salt,
        kdf_params=material_a.kdf_params,
        vault_public_key=material_b.vault_public_key,
        encrypted_vault_private_key=material_a.encrypted_vault_private_key,
        private_key_nonce=material_a.private_key_nonce,
        version=material_a.version,
    )
    with pytest.raises(VaultPassphraseError):
        unlock(PASSPHRASE, swapped)


# ── Encrypt without unlock; decrypt with unlock ───────────────────────────


def test_encrypt_for_vault_is_ingest_safe() -> None:
    """The core safety property: encryption requires only the public key,
    no passphrase, no unlock.
    """
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    envelope = encrypt_for_vault(b"my secret api key", material.vault_public_key)
    assert envelope["alg"]
    assert envelope["ephemeral_pub"]
    assert envelope["nonce"]
    assert envelope["ciphertext"]


def test_encrypt_rejects_malformed_public_key() -> None:
    with pytest.raises(ValueError):
        encrypt_for_vault(b"x", b"too-short")


def test_round_trip_recovers_plaintext() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    plaintext = b"ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    envelope = encrypt_for_vault(plaintext, material.vault_public_key)
    unlocked = unlock(PASSPHRASE, material)
    assert decrypt_from_vault(envelope, unlocked) == plaintext


def test_round_trip_with_aad_recovers_plaintext() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    plaintext = b"a-real-secret"
    aad = b"secret_id=42"
    envelope = encrypt_for_vault(plaintext, material.vault_public_key, aad=aad)
    unlocked = unlock(PASSPHRASE, material)
    assert decrypt_from_vault(envelope, unlocked) == plaintext


def test_decrypt_without_unlock_raises() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    envelope = encrypt_for_vault(b"x", material.vault_public_key)
    with pytest.raises(VaultLockedError):
        decrypt_from_vault(envelope, None)  # type: ignore[arg-type]


def test_decrypt_with_wrong_vault_raises() -> None:
    """An envelope created against vault A cannot be decrypted by vault B."""
    material_a = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    material_b = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    envelope = encrypt_for_vault(b"only-a-can-read", material_a.vault_public_key)
    unlocked_b = unlock(PASSPHRASE, material_b)
    with pytest.raises(ValueError):
        decrypt_from_vault(envelope, unlocked_b)


def test_decrypt_rejects_tampered_ciphertext() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    envelope = encrypt_for_vault(b"sensitive", material.vault_public_key)
    # Flip a byte in the b64-encoded ciphertext.
    tampered = dict(envelope)
    raw = envelope["ciphertext"]
    # Find a flippable index that produces valid b64; just toggle a known
    # mid-character (the b64 alphabet wraps so this still decodes).
    tampered["ciphertext"] = ("A" if raw[0] != "A" else "B") + raw[1:]
    unlocked = unlock(PASSPHRASE, material)
    with pytest.raises(ValueError):
        decrypt_from_vault(tampered, unlocked)


def test_decrypt_rejects_aad_mismatch() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    envelope = encrypt_for_vault(b"bound-to-id-7", material.vault_public_key, aad=b"id=7")
    # Caller modifies the envelope to claim a different AAD on decrypt.
    forged = dict(envelope)
    from base64 import urlsafe_b64encode

    forged["aad_b64"] = urlsafe_b64encode(b"id=8").decode("ascii")
    unlocked = unlock(PASSPHRASE, material)
    with pytest.raises(ValueError):
        decrypt_from_vault(forged, unlocked)


def test_decrypt_rejects_unknown_alg() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    envelope = encrypt_for_vault(b"x", material.vault_public_key)
    envelope["alg"] = "made-up-alg"
    unlocked = unlock(PASSPHRASE, material)
    with pytest.raises(ValueError):
        decrypt_from_vault(envelope, unlocked)


def test_nonce_is_unique_across_encrypts() -> None:
    """Two encrypts of the same plaintext must produce different envelopes
    (otherwise the system leaks equality of secrets).
    """
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    a = encrypt_for_vault(b"same-input", material.vault_public_key)
    b = encrypt_for_vault(b"same-input", material.vault_public_key)
    assert a["nonce"] != b["nonce"]
    assert a["ephemeral_pub"] != b["ephemeral_pub"]
    assert a["ciphertext"] != b["ciphertext"]


# ── KEK derivation ────────────────────────────────────────────────────────


def test_derive_kek_is_deterministic() -> None:
    salt = b"\x01" * 32
    a = derive_kek(PASSPHRASE, salt, kdf_params=TEST_KDF_PARAMS)
    b = derive_kek(PASSPHRASE, salt, kdf_params=TEST_KDF_PARAMS)
    assert a == b
    assert len(a) == 32


def test_derive_kek_changes_with_salt() -> None:
    a = derive_kek(PASSPHRASE, b"\x01" * 32, kdf_params=TEST_KDF_PARAMS)
    b = derive_kek(PASSPHRASE, b"\x02" * 32, kdf_params=TEST_KDF_PARAMS)
    assert a != b


def test_derive_kek_changes_with_passphrase() -> None:
    salt = b"\x01" * 32
    a = derive_kek(PASSPHRASE, salt, kdf_params=TEST_KDF_PARAMS)
    b = derive_kek(WRONG_PASSPHRASE, salt, kdf_params=TEST_KDF_PARAMS)
    assert a != b


# ── Passphrase rotation ───────────────────────────────────────────────────


def test_change_passphrase_preserves_decrypt_access() -> None:
    """Rotation re-wraps the private key but the keypair itself doesn't
    change. Envelopes created before rotation must still decrypt after.
    """
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    envelope = encrypt_for_vault(b"survives-rotation", material.vault_public_key)

    new_passphrase = "freshly-rotated-pass-phrase"
    rotated = change_passphrase(
        PASSPHRASE,
        new_passphrase,
        material,
        new_kdf_params=TEST_KDF_PARAMS,
    )
    assert rotated.vault_public_key == material.vault_public_key
    assert rotated.salt != material.salt
    assert rotated.encrypted_vault_private_key != material.encrypted_vault_private_key

    unlocked_new = unlock(new_passphrase, rotated)
    assert decrypt_from_vault(envelope, unlocked_new) == b"survives-rotation"


def test_change_passphrase_invalidates_old_passphrase() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    rotated = change_passphrase(
        PASSPHRASE,
        "new-pass",
        material,
        new_kdf_params=TEST_KDF_PARAMS,
    )
    with pytest.raises(VaultPassphraseError):
        unlock(PASSPHRASE, rotated)


def test_change_passphrase_requires_correct_old_passphrase() -> None:
    material = initialize_vault(PASSPHRASE, kdf_params=TEST_KDF_PARAMS)
    with pytest.raises(VaultPassphraseError):
        change_passphrase(
            WRONG_PASSPHRASE,
            "new-pass",
            material,
            new_kdf_params=TEST_KDF_PARAMS,
        )


# ── Defaults sanity ───────────────────────────────────────────────────────


def test_default_kdf_params_are_present() -> None:
    """If someone removes a param, the failure should surface here, not
    silently weaken production vaults.
    """
    assert DEFAULT_KDF_PARAMS["iterations"] >= 3
    assert DEFAULT_KDF_PARAMS["lanes"] >= 4
    assert DEFAULT_KDF_PARAMS["memory_cost_kib"] >= 65536
