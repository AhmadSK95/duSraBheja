"""Application-level encryption and signed state helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config import settings


def _decode_master_key() -> bytes:
    raw_value = (settings.encryption_master_key or "").strip()
    if not raw_value:
        raise RuntimeError("ENCRYPTION_MASTER_KEY is not configured")
    try:
        decoded = base64.urlsafe_b64decode(raw_value.encode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError("ENCRYPTION_MASTER_KEY must be urlsafe-base64 encoded") from exc
    if len(decoded) != 32:
        raise RuntimeError("ENCRYPTION_MASTER_KEY must decode to 32 bytes")
    return decoded


def encrypt_text(plaintext: str, *, associated_data: str | None = None) -> dict:
    key = _decode_master_key()
    nonce = os.urandom(12)
    aad = (associated_data or "").encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return {
        "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("utf-8"),
        "nonce": base64.urlsafe_b64encode(nonce).decode("utf-8"),
        "checksum": hashlib.sha256(plaintext.encode("utf-8")).hexdigest(),
    }


def decrypt_text(ciphertext: str, nonce: str, *, associated_data: str | None = None) -> str:
    key = _decode_master_key()
    aad = (associated_data or "").encode("utf-8")
    decrypted = AESGCM(key).decrypt(
        base64.urlsafe_b64decode(nonce.encode("utf-8")),
        base64.urlsafe_b64decode(ciphertext.encode("utf-8")),
        aad,
    )
    return decrypted.decode("utf-8")


def _state_secret() -> bytes:
    raw_value = (settings.api_token or "").strip()
    if not raw_value:
        raise RuntimeError("API_TOKEN must be configured")
    return raw_value.encode("utf-8")


def sign_state(payload: dict, *, ttl_minutes: int = 15) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    envelope = {
        "payload": payload,
        "exp": expires_at.isoformat(),
    }
    serialized = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_state_secret(), serialized, hashlib.sha256).hexdigest()
    token_payload = base64.urlsafe_b64encode(serialized).decode("utf-8")
    return f"{token_payload}.{signature}"


def verify_state(token: str) -> dict:
    try:
        encoded, provided_signature = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("Malformed state token") from exc

    serialized = base64.urlsafe_b64decode(encoded.encode("utf-8"))
    expected_signature = hmac.new(_state_secret(), serialized, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise ValueError("Invalid state signature")

    envelope = json.loads(serialized.decode("utf-8"))
    expires_at = datetime.fromisoformat(envelope["exp"])
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("Expired state token")
    return dict(envelope.get("payload") or {})
