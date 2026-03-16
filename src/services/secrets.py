"""Secret detection, vault storage, and owner-verified access."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from secrets import compare_digest
from types import SimpleNamespace

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


def _value_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _supports_canonical_secret_store(session: AsyncSession) -> bool:
    return hasattr(session, "execute") and hasattr(session, "get")


async def _record_secret_access_audit(session: AsyncSession, **kwargs) -> None:
    if not _supports_canonical_secret_store(session):
        return
    await store.create_secret_access_audit(session, **kwargs)


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


async def _resolve_secret_identity(
    session: AsyncSession,
    *,
    secret_id: uuid.UUID | None = None,
    alias: str | None = None,
):
    if not _supports_canonical_secret_store(session):
        return None
    identity = None
    if secret_id:
        identity = await store.get_secret_identity(session, secret_id)
        if identity is None:
            secret_record = await store.get_secret_record(session, secret_id)
            if secret_record:
                identity = await store.get_secret_identity_by_shadow_secret(session, secret_record.id)
    elif alias:
        identity = await store.get_secret_identity_by_label(session, alias)
        if identity is None:
            secret_record = await store.get_secret_record_by_alias(session, alias)
            if secret_record:
                identity = await store.get_secret_identity_by_shadow_secret(session, secret_record.id)
    return identity


async def _ensure_secret_shadow_record(
    session: AsyncSession,
    *,
    identity,
    label: str,
    secret_type: str,
    ciphertext: str,
    nonce: str,
    checksum: str,
    masked_preview: str,
    aliases: list[str],
    owner_scope: str,
    thread_refs: list[str],
    entity_refs: list[str],
    source_refs: list[str],
    metadata_: dict | None = None,
):
    source_ref = f"identity:{identity.id}"
    shadow = await store.upsert_secret_record(
        session,
        source_kind="secret_identity",
        source_ref=source_ref,
        secret_type=secret_type,
        label=label[:240],
        ciphertext=ciphertext,
        nonce=nonce,
        checksum=checksum,
        masked_preview=masked_preview,
        owner_scope=owner_scope,
        thread_refs=thread_refs,
        entity_refs=entity_refs,
        source_refs=source_refs,
        rotation_metadata=metadata_ or {},
        metadata_=metadata_ or {},
        aliases=aliases,
    )
    await store.update_secret_identity(session, identity.id, shadow_secret_id=shadow.id)
    return shadow


async def store_secret_value(
    session: AsyncSession,
    *,
    label: str,
    value: str,
    source_kind: str,
    source_ref: str,
    secret_type: str = "credential",
    username: str | None = None,
    aliases: list[str] | None = None,
    owner_scope: str = "owner",
    thread_refs: list[str] | None = None,
    entity_refs: list[str] | None = None,
    notes: str | None = None,
    metadata_: dict | None = None,
) -> tuple[object, object, bool]:
    if not _supports_canonical_secret_store(session):
        encrypted = encrypt_text(value, associated_data=f"secret:{source_kind}:{source_ref}")
        record = await store.upsert_secret_record(
            session,
            source_kind=source_kind,
            source_ref=source_ref,
            secret_type=secret_type,
            label=label[:240],
            ciphertext=encrypted["ciphertext"],
            nonce=encrypted["nonce"],
            checksum=encrypted["checksum"],
            masked_preview=_mask_secret(value),
            owner_scope=owner_scope,
            thread_refs=list(thread_refs or []),
            entity_refs=list(entity_refs or []),
            source_refs=[source_ref],
            rotation_metadata=metadata_ or {},
            metadata_=metadata_ or {},
            aliases=[alias for alias in [label, *(aliases or [])] if alias],
        )
        identity = SimpleNamespace(
            id=record.id,
            label=getattr(record, "label", label),
            category=secret_type,
            current_version_id=record.id,
            shadow_secret_id=record.id,
            owner_scope=owner_scope,
            aliases=[alias for alias in [label, *(aliases or [])] if alias],
        )
        version = SimpleNamespace(
            id=record.id,
            secret_type=secret_type,
            username=username,
            masked_preview=getattr(record, "masked_preview", _mask_secret(value)),
            is_current=True,
            created_at=_utcnow(),
            superseded_at=None,
        )
        return identity, version, True

    encrypted = encrypt_text(value, associated_data=f"secret:{source_kind}:{source_ref}")
    alias_values = [label, *(aliases or [])]
    identity = await store.get_secret_identity_by_label(session, label)
    if identity is None:
        identity = await store.upsert_secret_identity(
            session,
            label=label,
            category=secret_type,
            owner_scope=owner_scope,
            aliases=[alias for alias in alias_values if alias],
            thread_refs=list(thread_refs or []),
            entity_refs=list(entity_refs or []),
            metadata_=metadata_ or {},
        )

    current_versions = await store.list_secret_versions(session, identity_id=identity.id, limit=5)
    current_version = next((version for version in current_versions if version.is_current), current_versions[0] if current_versions else None)
    fingerprint = _value_fingerprint(value)
    is_new_version = True
    if current_version:
        existing_fingerprint = str((current_version.metadata_ or {}).get("value_fingerprint") or "")
        existing_username = (current_version.username or "").strip()
        if existing_fingerprint == fingerprint and existing_username == (username or "").strip():
            is_new_version = False

    if current_version and is_new_version:
        await store.clear_current_secret_versions(session, identity_id=identity.id)

    shadow = await _ensure_secret_shadow_record(
        session,
        identity=identity,
        label=label,
        secret_type=secret_type,
        ciphertext=encrypted["ciphertext"],
        nonce=encrypted["nonce"],
        checksum=encrypted["checksum"],
        masked_preview=_mask_secret(value),
        aliases=[alias for alias in alias_values if alias],
        owner_scope=owner_scope,
        thread_refs=list(thread_refs or []),
        entity_refs=list(entity_refs or []),
        source_refs=[source_ref],
        metadata_={
            **(metadata_ or {}),
            "username": username,
            "value_fingerprint": fingerprint,
            "identity_id": str(identity.id),
        },
    )

    if not is_new_version and current_version:
        await store.update_secret_identity(
            session,
            identity.id,
            aliases=[alias for alias in alias_values if alias],
            category=secret_type,
            owner_scope=owner_scope,
            current_version_id=current_version.id,
            shadow_secret_id=shadow.id,
        )
        return identity, current_version, False

    version = await store.create_secret_version(
        session,
        identity_id=identity.id,
        source_kind=source_kind,
        source_ref=source_ref,
        secret_type=secret_type,
        username=username,
        ciphertext=encrypted["ciphertext"],
        nonce=encrypted["nonce"],
        checksum=encrypted["checksum"],
        masked_preview=_mask_secret(value),
        source_refs=[source_ref],
        notes=notes,
        metadata_={**(metadata_ or {}), "value_fingerprint": fingerprint},
        is_current=True,
    )
    await store.update_secret_identity(
        session,
        identity.id,
        aliases=[alias for alias in alias_values if alias],
        category=secret_type,
        owner_scope=owner_scope,
        current_version_id=version.id,
        shadow_secret_id=shadow.id,
    )
    await _record_secret_access_audit(
        session,
        requester="system",
        action="secret_version_stored",
        identity_id=identity.id,
        version_id=version.id,
        purpose=label,
        metadata_={"source_kind": source_kind, "source_ref": source_ref},
    )
    return identity, version, True


async def list_secret_versions_for_identity(
    session: AsyncSession,
    *,
    secret_id: uuid.UUID | None = None,
    alias: str | None = None,
) -> tuple[object, list]:
    identity = await _resolve_secret_identity(session, secret_id=secret_id, alias=alias)
    if identity is None:
        raise ValueError("Secret target not found.")
    versions = await store.list_secret_versions(session, identity_id=identity.id, limit=50)
    return identity, versions


async def reveal_secret_for_owner_dm(
    session: AsyncSession,
    *,
    secret_id: uuid.UUID | None = None,
    alias: str | None = None,
    version: str = "latest",
) -> dict:
    identity, versions = await list_secret_versions_for_identity(session, secret_id=secret_id, alias=alias)
    if not versions:
        raise ValueError("Secret has no stored versions.")
    target = versions[0]
    if version not in {"latest", "current"}:
        try:
            index = int(version)
        except ValueError as exc:
            raise ValueError("Version must be latest, current, or a numeric history index.") from exc
        if index < 1 or index > len(versions):
            raise ValueError("Requested version is out of range.")
        target = versions[index - 1]
    plaintext = decrypt_text(
        target.ciphertext,
        target.nonce,
        associated_data=f"secret:{target.source_kind}:{target.source_ref}",
    )
    await _record_secret_access_audit(
        session,
        requester="owner_dm",
        action="owner_dm_reveal",
        identity_id=identity.id,
        version_id=target.id,
        purpose="owner_dm_direct_reveal",
        metadata_={"version_selector": version},
    )
    return {
        "identity_id": str(identity.id),
        "label": identity.label,
        "category": identity.category,
        "username": target.username,
        "value": plaintext,
        "masked_preview": target.masked_preview,
        "version_id": str(target.id),
        "created_at": target.created_at.isoformat(),
    }


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
        aliases = [candidate.label, *candidate.aliases, *(alias_hints or [])]
        label = f"{purpose_label}: {candidate.label}" if purpose_label else candidate.label
        identity, version, _created = await store_secret_value(
            session,
            label=label[:240],
            value=candidate.value,
            source_kind=source_kind,
            source_ref=f"{source_ref}:{index}",
            secret_type=candidate.secret_type,
            owner_scope="owner",
            thread_refs=list(thread_refs or []),
            entity_refs=list(entity_refs or []),
            aliases=[alias for alias in aliases if alias],
            metadata_={"purpose_label": purpose_label},
        )
        await _record_secret_access_audit(
            session,
            requester="system",
            action="secret_captured",
            identity_id=identity.id,
            version_id=version.id,
            purpose=purpose_label,
            metadata_={"source_kind": source_kind, "source_ref": source_ref},
        )
        await store.create_secret_audit_entry(
            session,
            requester="system",
            action="secret_captured",
            secret_id=getattr(identity, "shadow_secret_id", None) or identity.id,
            purpose=purpose_label,
            metadata_={"source_kind": source_kind, "source_ref": source_ref},
        )
        records.append(identity)
    return records, redact_secret_candidates(text, candidates)


async def capture_secret_drop(
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
    records, redacted = await capture_secrets_from_text(
        session,
        text=text,
        source_kind=source_kind,
        source_ref=source_ref,
        purpose_label=purpose_label,
        alias_hints=alias_hints,
        thread_refs=thread_refs,
        entity_refs=entity_refs,
    )
    if records or not (text or "").strip():
        return records, redacted

    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")[:120]
    label = first_line or purpose_label or "Owner secret drop"
    identity, version, _created = await store_secret_value(
        session,
        label=label,
        value=text,
        source_kind=source_kind,
        source_ref=source_ref,
        secret_type="secret_note",
        owner_scope="owner",
        thread_refs=list(thread_refs or []),
        entity_refs=list(entity_refs or []),
        metadata_={"purpose_label": purpose_label, "ingest_mode": "owner_dm_drop"},
        aliases=[label, *(alias_hints or [])],
    )
    await _record_secret_access_audit(
        session,
        requester="system",
        action="secret_drop_captured",
        identity_id=identity.id,
        version_id=version.id,
        purpose=purpose_label,
        metadata_={"source_kind": source_kind, "source_ref": source_ref},
    )
    await store.create_secret_audit_entry(
        session,
        requester="system",
        action="secret_drop_captured",
        secret_id=getattr(identity, "shadow_secret_id", None) or identity.id,
        purpose=purpose_label,
        metadata_={"source_kind": source_kind, "source_ref": source_ref},
    )
    return [identity], f"[REDACTED SECRET DROP: {label}]"


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
    identity = await _resolve_secret_identity(session, secret_id=secret_id, alias=alias)
    secret_record = None
    if identity and identity.shadow_secret_id:
        secret_record = await store.get_secret_record(session, identity.shadow_secret_id)
    elif secret_id:
        secret_record = await store.get_secret_record(session, secret_id)
    elif alias:
        secret_record = await store.get_secret_record_by_alias(session, alias)
    if identity is None and secret_record is not None:
        identity = SimpleNamespace(
            id=secret_record.id,
            label=secret_record.label,
            category=secret_record.secret_type,
            current_version_id=secret_record.id,
            shadow_secret_id=secret_record.id,
        )
    if identity is None and (secret_id or alias):
        raise ValueError("Secret target not found.")
    if identity is None:
        raise ValueError("Secret target not found.")
    if secret_record is None and identity.shadow_secret_id:
        secret_record = await store.get_secret_record(session, identity.shadow_secret_id)
    if secret_record is None:
        raise ValueError("Secret target not found.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = _utcnow() + timedelta(minutes=settings.secret_challenge_ttl_minutes)
    challenge = await store.create_secret_access_challenge(
        session,
        secret_id=secret_record.id,
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
        secret_id=secret_record.id,
        challenge_id=challenge.id,
        purpose=purpose,
        metadata_={"delivery": "discord_dm"},
    )
    await _record_secret_access_audit(
        session,
        requester=requester,
        action="challenge_requested",
        identity_id=identity.id,
        version_id=identity.current_version_id,
        purpose=purpose,
        metadata_={"delivery": "discord_dm"},
    )
    target_label = identity.label
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
        "secret_id": str(identity.id),
        "masked_preview": secret_record.masked_preview,
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
    identity = await _resolve_secret_identity(session, secret_id=secret_id)
    secret_record = None
    if identity and identity.shadow_secret_id:
        secret_record = await store.get_secret_record(session, identity.shadow_secret_id)
    if not secret_record:
        secret_record = await store.get_secret_record(session, secret_id)
        if secret_record:
            identity = SimpleNamespace(
                id=secret_record.id,
                label=secret_record.label,
                category=secret_record.secret_type,
                current_version_id=secret_record.id,
                shadow_secret_id=secret_record.id,
            )
    if not identity or not secret_record:
        raise ValueError("Secret not found.")
    grant = await store.find_secret_access_grant_by_hash(session, grant_hash=_hash_token(grant_token.strip()))
    now = _utcnow()
    if not grant:
        raise ValueError("Secret access grant not found.")
    if grant.secret_id != secret_record.id:
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
    await _record_secret_access_audit(
        session,
        requester=requester,
        action="secret_revealed",
        identity_id=identity.id,
        version_id=identity.current_version_id,
        purpose=grant.purpose,
        metadata_={"grant_id": str(grant.id)},
    )
    current_version = None
    if identity.current_version_id and _supports_canonical_secret_store(session):
        current_version = await store.get_secret_version(session, identity.current_version_id)
    return {
        "secret_id": str(secret_record.id),
        "label": identity.label,
        "secret_type": identity.category,
        "username": current_version.username if current_version else None,
        "masked_preview": secret_record.masked_preview,
        "value": plaintext,
        "revealed_at": now.isoformat(),
    }


async def build_secret_inventory(session: AsyncSession) -> list[dict]:
    identities = await store.list_secret_identities(session, limit=200)
    inventory = []
    for identity in identities:
        versions = await store.list_secret_versions(session, identity_id=identity.id, limit=20)
        current_version = next((version for version in versions if version.is_current), versions[0] if versions else None)
        inventory.append(
            {
                "id": str(identity.id),
                "label": identity.label,
                "category": identity.category,
                "masked_preview": current_version.masked_preview if current_version else "n/a",
                "username": current_version.username if current_version else None,
                "owner_scope": identity.owner_scope,
                "aliases": list(identity.aliases or []),
                "thread_refs": list(identity.thread_refs or []),
                "entity_refs": list(identity.entity_refs or []),
                "version_count": len(versions),
                "current_version_id": str(identity.current_version_id) if identity.current_version_id else None,
                "versions": [
                    {
                        "id": str(version.id),
                        "secret_type": version.secret_type,
                        "username": version.username,
                        "masked_preview": version.masked_preview,
                        "is_current": version.is_current,
                        "created_at": version.created_at.isoformat(),
                        "superseded_at": version.superseded_at.isoformat() if version.superseded_at else None,
                    }
                    for version in versions
                ],
                "created_at": identity.created_at.isoformat(),
                "updated_at": identity.updated_at.isoformat(),
            }
        )
    return inventory


async def describe_secret_history(
    session: AsyncSession,
    *,
    secret_id: uuid.UUID | None = None,
    alias: str | None = None,
) -> dict:
    identity, versions = await list_secret_versions_for_identity(session, secret_id=secret_id, alias=alias)
    return {
        "identity_id": str(identity.id),
        "label": identity.label,
        "category": identity.category,
        "aliases": list(identity.aliases or []),
        "versions": [
            {
                "index": index,
                "id": str(version.id),
                "secret_type": version.secret_type,
                "username": version.username,
                "masked_preview": version.masked_preview,
                "is_current": version.is_current,
                "created_at": version.created_at.isoformat(),
                "superseded_at": version.superseded_at.isoformat() if version.superseded_at else None,
            }
            for index, version in enumerate(versions, start=1)
        ],
    }
