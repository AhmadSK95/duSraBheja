"""Secret detection, vault storage, and owner-verified access."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from secrets import compare_digest

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.lib import store
from src.lib.crypto import decrypt_text, encrypt_text

DISCORD_API_BASE = "https://discord.com/api/v10"


@dataclass(slots=True)
class SecretCandidate:
    secret_type: str
    label: str
    value: str
    start: int
    end: int
    masked_preview: str
    aliases: list[str]


SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "anthropic_api_key",
        re.compile(r"(sk-ant-[A-Za-z0-9_\-]{20,})"),
        "Anthropic API key",
    ),
    (
        "openai_api_key",
        re.compile(r"(sk-[A-Za-z0-9]{20,})"),
        "OpenAI API key",
    ),
    (
        "github_pat",
        re.compile(r"(github_pat_[A-Za-z0-9_]{20,})"),
        "GitHub personal access token",
    ),
    (
        "generic_labeled_secret",
        re.compile(
            r"(?im)\b(?P<label>api[_ -]?key|token|password|passwd|secret|license[_ -]?key|username)\b\s*[:=]\s*(?P<value>[^\s\"']{6,})"
        ),
        "",
    ),
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def _normalize_alias(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    return "".join(ch if ch.isalnum() else "-" for ch in cleaned).strip("-")


def _hash_token(value: str) -> str:
    salt = settings.dashboard_session_secret or settings.api_token or settings.encryption_master_key or "brain-secret-salt"
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()


def extract_secret_candidates(text: str) -> list[SecretCandidate]:
    candidates: list[SecretCandidate] = []
    for secret_type, pattern, static_label in SECRET_PATTERNS:
        for match in pattern.finditer(text or ""):
            if secret_type == "generic_labeled_secret":
                label = (match.group("label") or "Secret").strip().replace("_", " ").title()
                value = match.group("value")
                start = match.start("value")
                end = match.end("value")
                aliases = [label]
            else:
                label = static_label
                value = match.group(1)
                start = match.start(1)
                end = match.end(1)
                aliases = [label]
            if not value:
                continue
            candidates.append(
                SecretCandidate(
                    secret_type=secret_type,
                    label=label,
                    value=value,
                    start=start,
                    end=end,
                    masked_preview=_mask_secret(value),
                    aliases=aliases,
                )
            )
    deduped: list[SecretCandidate] = []
    seen: set[tuple[str, str, int, int]] = set()
    for candidate in sorted(candidates, key=lambda item: (item.start, item.end)):
        key = (candidate.secret_type, candidate.value, candidate.start, candidate.end)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def redact_secret_candidates(text: str, candidates: list[SecretCandidate]) -> str:
    if not candidates:
        return text
    redacted = text
    for candidate in sorted(candidates, key=lambda item: item.start, reverse=True):
        replacement = f"[REDACTED SECRET: {candidate.label}]"
        redacted = redacted[: candidate.start] + replacement + redacted[candidate.end :]
    return redacted


async def capture_secrets_from_text(
    session: AsyncSession,
    *,
    text: str,
    source_kind: str,
    source_ref: str,
    purpose_label: str,
    alias_hints: list[str] | None = None,
    thread_refs: list[str] | None = None,
    entity_refs: list[str] | None = None,
) -> tuple[list, str]:
    candidates = extract_secret_candidates(text)
    if not candidates:
        return [], text
    records = []
    for index, candidate in enumerate(candidates, start=1):
        encrypted = encrypt_text(
            candidate.value,
            associated_data=f"secret:{source_kind}:{source_ref}:{index}",
        )
        aliases = [candidate.label, *candidate.aliases, *(alias_hints or [])]
        label = f"{purpose_label}: {candidate.label}" if purpose_label else candidate.label
        secret = await store.upsert_secret_record(
            session,
            source_kind=source_kind,
            source_ref=f"{source_ref}:{index}",
            secret_type=candidate.secret_type,
            label=label[:240],
            ciphertext=encrypted["ciphertext"],
            nonce=encrypted["nonce"],
            checksum=encrypted["checksum"],
            masked_preview=candidate.masked_preview,
            owner_scope="owner",
            thread_refs=list(thread_refs or []),
            entity_refs=list(entity_refs or []),
            source_refs=[source_ref],
            rotation_metadata={},
            metadata_={"purpose_label": purpose_label},
            aliases=[alias for alias in aliases if alias],
        )
        await store.create_secret_audit_entry(
            session,
            requester="system",
            action="secret_captured",
            secret_id=secret.id,
            purpose=purpose_label,
            status="ok",
            metadata_={"source_kind": source_kind, "source_ref": source_ref},
        )
        records.append(secret)
    return records, redact_secret_candidates(text, candidates)


async def _send_owner_discord_dm(message: str) -> None:
    if not settings.discord_token:
        raise RuntimeError("Discord bot token is not configured for secret challenges.")
    if not settings.discord_owner_user_id:
        raise RuntimeError("DISCORD_OWNER_USER_ID is not configured for secret challenges.")
    headers = {
        "Authorization": f"Bot {settings.discord_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(base_url=DISCORD_API_BASE, timeout=15) as client:
        dm_response = await client.post(
            "/users/@me/channels",
            headers=headers,
            json={"recipient_id": str(settings.discord_owner_user_id)},
        )
        dm_response.raise_for_status()
        channel_id = dm_response.json()["id"]
        message_response = await client.post(
            f"/channels/{channel_id}/messages",
            headers=headers,
            json={"content": message},
        )
        message_response.raise_for_status()


async def request_secret_challenge(
    session: AsyncSession,
    *,
    requester: str,
    purpose: str,
    secret_id: uuid.UUID | None = None,
    alias: str | None = None,
) -> dict:
    secret_record = None
    if secret_id:
        secret_record = await store.get_secret_record(session, secret_id)
    elif alias:
        secret_record = await store.get_secret_record_by_alias(session, alias)
    if secret_record is None and (secret_id or alias):
        raise ValueError("Secret target not found.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = _utcnow() + timedelta(minutes=settings.secret_challenge_ttl_minutes)
    challenge = await store.create_secret_access_challenge(
        session,
        secret_id=secret_record.id if secret_record else None,
        requester=requester,
        purpose=purpose,
        challenge_hash=_hash_token(code),
        expires_at=expires_at,
        max_attempts=settings.secret_challenge_max_attempts,
        metadata_={"delivery": "discord_dm"},
    )
    await store.create_secret_audit_entry(
        session,
        requester=requester,
        action="challenge_requested",
        secret_id=secret_record.id if secret_record else None,
        challenge_id=challenge.id,
        purpose=purpose,
        metadata_={"delivery": "discord_dm"},
    )
    target_label = secret_record.label if secret_record else "secret vault access"
    await _send_owner_discord_dm(
        "\n".join(
            [
                "Brain Secret Vault OTP",
                f"Purpose: {purpose}",
                f"Target: {target_label}",
                f"Code: {code}",
                f"Expires in: {settings.secret_challenge_ttl_minutes} minutes",
                f"Challenge ID: {challenge.id}",
            ]
        )
    )
    return {
        "challenge_id": str(challenge.id),
        "expires_at": expires_at.isoformat(),
        "delivery": "discord_dm",
        "secret_id": str(secret_record.id) if secret_record else None,
        "masked_preview": secret_record.masked_preview if secret_record else None,
    }


async def verify_secret_challenge(
    session: AsyncSession,
    *,
    requester: str,
    challenge_id: uuid.UUID,
    otp_code: str,
) -> dict:
    challenge = await store.get_secret_access_challenge(session, challenge_id)
    if not challenge:
        raise ValueError("Challenge not found.")
    now = _utcnow()
    if challenge.status not in {"pending", "verified"}:
        raise ValueError("Challenge is no longer active.")
    if challenge.expires_at <= now:
        await store.update_secret_access_challenge(session, challenge.id, status="expired")
        raise ValueError("Challenge expired.")
    attempts = int(challenge.attempts or 0) + 1
    if attempts > int(challenge.max_attempts or settings.secret_challenge_max_attempts):
        await store.update_secret_access_challenge(session, challenge.id, status="failed", attempts=attempts)
        raise ValueError("Challenge attempts exceeded.")
    if not compare_digest(challenge.challenge_hash, _hash_token(otp_code.strip())):
        await store.update_secret_access_challenge(session, challenge.id, attempts=attempts)
        await store.create_secret_audit_entry(
            session,
            requester=requester,
            action="challenge_verify_failed",
            secret_id=challenge.secret_id,
            challenge_id=challenge.id,
            purpose=challenge.purpose,
            status="failed",
        )
        raise ValueError("Invalid OTP code.")
    await store.update_secret_access_challenge(
        session,
        challenge.id,
        attempts=attempts,
        status="verified",
        verified_at=now,
    )
    raw_token = secrets.token_urlsafe(24)
    grant = await store.create_secret_access_grant(
        session,
        secret_id=challenge.secret_id,
        challenge_id=challenge.id,
        requester=requester,
        purpose=challenge.purpose,
        grant_hash=_hash_token(raw_token),
        expires_at=now + timedelta(seconds=settings.secret_access_grant_ttl_seconds),
        metadata_={"single_use": True},
    )
    await store.create_secret_audit_entry(
        session,
        requester=requester,
        action="challenge_verified",
        secret_id=challenge.secret_id,
        challenge_id=challenge.id,
        grant_id=grant.id,
        purpose=challenge.purpose,
        status="ok",
    )
    return {
        "grant_token": raw_token,
        "grant_id": str(grant.id),
        "expires_at": grant.expires_at.isoformat(),
        "secret_id": str(challenge.secret_id) if challenge.secret_id else None,
    }


async def reveal_secret_once(
    session: AsyncSession,
    *,
    requester: str,
    secret_id: uuid.UUID,
    grant_token: str,
) -> dict:
    secret_record = await store.get_secret_record(session, secret_id)
    if not secret_record:
        raise ValueError("Secret not found.")
    grant = await store.find_secret_access_grant_by_hash(session, grant_hash=_hash_token(grant_token.strip()))
    now = _utcnow()
    if not grant:
        raise ValueError("Secret access grant not found.")
    if grant.secret_id != secret_id:
        raise ValueError("Secret access grant does not match this secret.")
    if grant.status != "active":
        raise ValueError("Secret access grant is not active.")
    if grant.expires_at <= now:
        await store.update_secret_access_grant(session, grant.id, status="expired")
        raise ValueError("Secret access grant expired.")
    plaintext = decrypt_text(
        secret_record.ciphertext,
        secret_record.nonce,
        associated_data=f"secret:{secret_record.source_kind}:{secret_record.source_ref}",
    )
    await store.update_secret_access_grant(
        session,
        grant.id,
        status="used",
        used_at=now,
    )
    await store.create_secret_audit_entry(
        session,
        requester=requester,
        action="secret_revealed",
        secret_id=secret_record.id,
        challenge_id=grant.challenge_id,
        grant_id=grant.id,
        purpose=grant.purpose,
        status="ok",
    )
    return {
        "secret_id": str(secret_record.id),
        "label": secret_record.label,
        "secret_type": secret_record.secret_type,
        "masked_preview": secret_record.masked_preview,
        "value": plaintext,
        "revealed_at": now.isoformat(),
    }


async def build_secret_inventory(session: AsyncSession) -> list[dict]:
    secrets_list = await store.list_secret_records(session, limit=200)
    inventory = []
    for secret_record in secrets_list:
        aliases = await store.list_secret_alias_records(session, secret_id=secret_record.id, limit=20)
        inventory.append(
            {
                "id": str(secret_record.id),
                "label": secret_record.label,
                "secret_type": secret_record.secret_type,
                "masked_preview": secret_record.masked_preview,
                "owner_scope": secret_record.owner_scope,
                "aliases": [alias.alias for alias in aliases],
                "thread_refs": list(secret_record.thread_refs or []),
                "entity_refs": list(secret_record.entity_refs or []),
                "created_at": secret_record.created_at.isoformat(),
                "updated_at": secret_record.updated_at.isoformat(),
            }
        )
    return inventory
