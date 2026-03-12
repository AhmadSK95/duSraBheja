from __future__ import annotations

import base64

from src.lib import crypto


def test_encrypt_and_decrypt_round_trip(monkeypatch) -> None:
    master_key = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode("utf-8")
    monkeypatch.setattr(crypto.settings, "encryption_master_key", master_key)

    encrypted = crypto.encrypt_text("top secret", associated_data="test:aad")
    decrypted = crypto.decrypt_text(encrypted["ciphertext"], encrypted["nonce"], associated_data="test:aad")

    assert decrypted == "top secret"
    assert encrypted["checksum"]


def test_sign_and_verify_state_round_trip(monkeypatch) -> None:
    monkeypatch.setattr(crypto.settings, "api_token", "brain-state-secret")

    token = crypto.sign_state({"provider": "google", "redirect_uri": "http://localhost/callback"})
    payload = crypto.verify_state(token)

    assert payload["provider"] == "google"
    assert payload["redirect_uri"] == "http://localhost/callback"
