from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services import secrets as secret_service


def test_extract_and_redact_secret_candidates() -> None:
    text = "OPENAI_API_KEY=sk-abcDEF1234567890ABCDE and password: hunter22"

    candidates = secret_service.extract_secret_candidates(text)
    redacted = secret_service.redact_secret_candidates(text, candidates)

    assert len(candidates) >= 2
    assert "sk-abcDEF1234567890ABCDE" not in redacted
    assert "hunter22" not in redacted
    assert "[REDACTED SECRET:" in redacted


@pytest.mark.asyncio
async def test_secret_challenge_verify_and_reveal_flow(monkeypatch) -> None:
    master_key = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode("utf-8")
    monkeypatch.setattr(secret_service.settings, "encryption_master_key", master_key)
    monkeypatch.setattr(secret_service.settings, "dashboard_session_secret", "unit-test-secret")
    monkeypatch.setattr(secret_service.settings, "secret_challenge_ttl_minutes", 5)
    monkeypatch.setattr(secret_service.settings, "secret_access_grant_ttl_seconds", 120)
    monkeypatch.setattr(secret_service.settings, "secret_challenge_max_attempts", 5)

    encrypted = secret_service.encrypt_text("super-secret-value", associated_data="secret:manual:test-secret")
    secret_id = uuid4()
    state: dict[str, object] = {
        "secret": SimpleNamespace(
            id=secret_id,
            source_kind="manual",
            source_ref="test-secret",
            label="Test Secret",
            secret_type="api_key",
            ciphertext=encrypted["ciphertext"],
            nonce=encrypted["nonce"],
            checksum=encrypted["checksum"],
            masked_preview="supe…alue",
        )
    }
    sent_messages: list[str] = []

    async def fake_send(message: str) -> None:
        sent_messages.append(message)

    async def fake_get_secret_record(session, secret_id_value):
        return state["secret"] if secret_id_value == secret_id else None

    async def fake_get_secret_record_by_alias(session, alias):
        return None

    async def fake_create_challenge(session, **kwargs):
        challenge = SimpleNamespace(
            id=uuid4(),
            attempts=0,
            status="pending",
            verified_at=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            **kwargs,
        )
        state["challenge"] = challenge
        return challenge

    async def fake_get_challenge(session, challenge_id):
        challenge = state.get("challenge")
        return challenge if challenge and challenge.id == challenge_id else None

    async def fake_update_challenge(session, challenge_id, **values):
        challenge = state["challenge"]
        for key, value in values.items():
            setattr(challenge, key, value)
        state["challenge"] = challenge
        return challenge

    async def fake_create_grant(session, **kwargs):
        grant = SimpleNamespace(
            id=uuid4(),
            status="active",
            used_at=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            **kwargs,
        )
        state["grant"] = grant
        return grant

    async def fake_find_grant_by_hash(session, *, grant_hash):
        grant = state.get("grant")
        if grant and grant.grant_hash == grant_hash:
            return grant
        return None

    async def fake_update_grant(session, grant_id, **values):
        grant = state["grant"]
        for key, value in values.items():
            setattr(grant, key, value)
        state["grant"] = grant
        return grant

    async def fake_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(secret_service, "_send_owner_discord_dm", fake_send)
    monkeypatch.setattr(secret_service.store, "get_secret_record", fake_get_secret_record)
    monkeypatch.setattr(secret_service.store, "get_secret_record_by_alias", fake_get_secret_record_by_alias)
    monkeypatch.setattr(secret_service.store, "create_secret_access_challenge", fake_create_challenge)
    monkeypatch.setattr(secret_service.store, "get_secret_access_challenge", fake_get_challenge)
    monkeypatch.setattr(secret_service.store, "update_secret_access_challenge", fake_update_challenge)
    monkeypatch.setattr(secret_service.store, "create_secret_access_grant", fake_create_grant)
    monkeypatch.setattr(secret_service.store, "find_secret_access_grant_by_hash", fake_find_grant_by_hash)
    monkeypatch.setattr(secret_service.store, "update_secret_access_grant", fake_update_grant)
    monkeypatch.setattr(secret_service.store, "create_secret_audit_entry", fake_audit)

    challenge_payload = await secret_service.request_secret_challenge(
        object(),
        requester="dashboard:ahmad",
        purpose="Need the API key for a deploy test",
        secret_id=secret_id,
    )

    assert sent_messages
    otp_code = sent_messages[0].split("Code: ", 1)[1].splitlines()[0].strip()
    challenge_id = challenge_payload["challenge_id"]

    verify_payload = await secret_service.verify_secret_challenge(
        object(),
        requester="dashboard:ahmad",
        challenge_id=uuid4() if False else state["challenge"].id,
        otp_code=otp_code,
    )

    assert verify_payload["secret_id"] == str(secret_id)
    assert state["challenge"].status == "verified"

    reveal_payload = await secret_service.reveal_secret_once(
        object(),
        requester="dashboard:ahmad",
        secret_id=secret_id,
        grant_token=verify_payload["grant_token"],
    )

    assert challenge_id == str(state["challenge"].id)
    assert reveal_payload["value"] == "super-secret-value"
    assert state["grant"].status == "used"


@pytest.mark.asyncio
async def test_verify_secret_challenge_rejects_expired_code(monkeypatch) -> None:
    challenge = SimpleNamespace(
        id=uuid4(),
        secret_id=uuid4(),
        requester="dashboard:ahmad",
        purpose="expired test",
        challenge_hash=secret_service._hash_token("123456"),
        status="pending",
        attempts=0,
        max_attempts=5,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    async def fake_get_challenge(session, challenge_id):
        return challenge

    async def fake_update_challenge(session, challenge_id, **values):
        for key, value in values.items():
            setattr(challenge, key, value)
        return challenge

    monkeypatch.setattr(secret_service.store, "get_secret_access_challenge", fake_get_challenge)
    monkeypatch.setattr(secret_service.store, "update_secret_access_challenge", fake_update_challenge)

    with pytest.raises(ValueError, match="expired"):
        await secret_service.verify_secret_challenge(
            object(),
            requester="dashboard:ahmad",
            challenge_id=challenge.id,
            otp_code="123456",
        )

    assert challenge.status == "expired"
