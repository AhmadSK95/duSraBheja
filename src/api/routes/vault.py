"""Dashboard vault routes — setup + unlock flows.

Routes:
- ``GET  /dashboard/vault/``           → smart index: setup / unlock / list
- ``GET  /dashboard/vault/setup``      → render setup form (step 1.3)
- ``POST /dashboard/vault/setup/generate`` → AJAX diceware suggestion
- ``POST /dashboard/vault/setup``      → handle setup submission
- ``GET  /dashboard/vault/unlock``     → render unlock form (step 1.4)
- ``POST /dashboard/vault/unlock``     → handle unlock submission
- ``POST /dashboard/vault/lock``       → wipe in-memory + DB unlock row

The list view + reveal flow land in step 1.5.
"""

from __future__ import annotations

import hashlib
import html
import logging
import uuid
from pathlib import Path
from string import Template

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.api.dashboard_ui import render_dashboard_shell
from src.config import settings
from src.database import async_session
from src.lib import diceware, vault_crypto
from src.lib.auth import require_dashboard_token
from src.services import vault as vault_service

router = APIRouter()
log = logging.getLogger("brain-vault-routes")

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
_SETUP_TEMPLATE = Template(
    (TEMPLATE_DIR / "dashboard_vault_setup.html").read_text(encoding="utf-8")
)
_UNLOCK_TEMPLATE = Template(
    (TEMPLATE_DIR / "dashboard_vault_unlock.html").read_text(encoding="utf-8")
)


# ── Session identifier (stable per browser session) ───────────────────────


def _unlock_session_id(request: Request) -> str:
    """Stable per-session identifier for vault unlock tracking.

    Prefers a UUID stored in the dashboard session cookie (browser flow).
    Falls back to a SHA-256 of the Bearer token (service-to-service /
    test flow), so unlock state is keyed consistently in both cases.

    The empty string is never returned — callers can compare against
    "" without worrying about ambiguity.
    """
    session = getattr(request, "session", None)
    if session is not None:
        sid = session.get("vault_unlock_session_id")
        if not sid:
            sid = str(uuid.uuid4())
            session["vault_unlock_session_id"] = sid
        return sid
    auth = request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ").strip()
        return "bearer:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    # No session, no Bearer. Synthesize a per-request id; the resulting
    # unlock will be unreachable from any subsequent request (which is
    # exactly what we want when there's no auth context).
    return "unauth:" + uuid.uuid4().hex


def _device_fingerprint(request: Request) -> str:
    """Hash of UA + IP family. Owner-visible label for the unlock session;
    not used as a security control.
    """
    ua = request.headers.get("user-agent") or ""
    ip = (request.client.host if request.client else "") or ""
    raw = f"{ua}|{ip}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _client_ip(request: Request) -> str | None:
    if request.client:
        return request.client.host
    return None


def _safe_next(next_path: str | None) -> str:
    """Constrain ``?next=`` to internal dashboard paths only. Stops the
    unlock route from being weaponized for open-redirect phishing.
    """
    if not next_path:
        return "/dashboard/"
    if next_path == "/dashboard" or next_path.startswith("/dashboard/"):
        return next_path
    return "/dashboard/"

# Server-side floors. The client form has lenient HTML constraints
# (minlength=12) so it doesn't block the diceware suggestion; the real
# validation happens here.
MIN_PASSPHRASE_LEN = 16  # for owner-typed (non-diceware) phrases
MIN_ENTROPY_BITS = 50.0  # diceware-or-typed alike


def _wordlist_path() -> str | None:
    """Resolve the configured EFF wordlist path, if any. Settings key may
    not exist yet (added later when we bundle EFF Large); fall back to
    builtin in that case.
    """
    raw = getattr(settings, "diceware_wordlist_path", None) or None
    return str(raw) if raw else None


def _render_setup(*, error: str | None = None, suggested: str | None = None) -> HTMLResponse:
    """Render the setup page. Generates a fresh diceware suggestion when one
    isn't supplied. Errors render inline below the form fields.
    """
    if suggested is None:
        try:
            suggested = diceware.generate_passphrase(wordlist_path=_wordlist_path())
        except Exception:
            log.exception("diceware suggestion failed; rendering form without one")
            suggested = ""
    error_html = (
        f'<div class="vault-setup-feedback" data-state="error">{html.escape(error)}</div>'
        if error
        else ""
    )
    content = _SETUP_TEMPLATE.safe_substitute(
        suggested_passphrase=html.escape(suggested),
        error_html=error_html,
    )
    return render_dashboard_shell(
        title="Vault setup",
        token="",
        active_page="vault",
        hero_kicker="Vault",
        hero_title="Set the passphrase that protects every other secret.",
        hero_subtitle="One-time setup. The passphrase lives only in your head.",
        content_html=content,
    )


# ── GET setup ─────────────────────────────────────────────────────────────


@router.get(
    "/dashboard/vault/setup",
    dependencies=[Depends(require_dashboard_token)],
    response_class=HTMLResponse,
    response_model=None,
)
async def vault_setup_get() -> HTMLResponse | RedirectResponse:
    async with async_session() as session:
        already = await vault_service.is_vault_initialized(session)
    if already:
        # No need to re-set up an existing vault — send to home.
        return RedirectResponse(url="/dashboard/", status_code=303)
    return _render_setup()


# ── POST suggestion endpoint ──────────────────────────────────────────────


@router.post(
    "/dashboard/vault/setup/generate",
    dependencies=[Depends(require_dashboard_token)],
)
async def vault_setup_generate() -> JSONResponse:
    try:
        passphrase = diceware.generate_passphrase(wordlist_path=_wordlist_path())
    except Exception as exc:
        log.exception("diceware generation failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"passphrase": passphrase})


# ── POST setup submission ─────────────────────────────────────────────────


@router.post(
    "/dashboard/vault/setup",
    dependencies=[Depends(require_dashboard_token)],
    response_class=HTMLResponse,
    response_model=None,
)
async def vault_setup_post(
    request: Request,
    passphrase: str = Form(default=""),
    passphrase_confirm: str = Form(default=""),
    acknowledged_backup: str = Form(default=""),
) -> HTMLResponse | RedirectResponse:
    # ─ Validation, in order of cheapest-first ─
    if not passphrase or not passphrase_confirm:
        return _render_setup(error="Both passphrase fields are required.")

    if passphrase != passphrase_confirm:
        # Don't echo the values back into the form via suggested= — keep the
        # form empty after a mismatch so the owner re-types fresh.
        return _render_setup(error="Passphrase and confirmation do not match.", suggested="")

    if acknowledged_backup != "yes":
        return _render_setup(
            error="You must confirm you've stored the passphrase safely before continuing.",
            suggested="",
        )

    # Strength: either it parses as ≥ 6-word diceware against the wordlist,
    # or it's ≥ 16 chars AND has ≥ MIN_ENTROPY_BITS character-class entropy.
    bits = diceware.estimate_entropy_bits(passphrase, wordlist_path=_wordlist_path())
    tokens = [t for t in passphrase.replace(" ", "-").split("-") if t]
    looks_diceware = len(tokens) >= 6
    if not looks_diceware and len(passphrase) < MIN_PASSPHRASE_LEN:
        return _render_setup(
            error=(
                f"Passphrase too short. Minimum {MIN_PASSPHRASE_LEN} characters, "
                "or use the diceware suggestion above."
            ),
            suggested="",
        )
    if bits < MIN_ENTROPY_BITS:
        return _render_setup(
            error=(
                "Passphrase is too weak. Use the suggested diceware above or pick "
                "something longer with mixed character types."
            ),
            suggested="",
        )

    # ─ Persist ─
    async with async_session() as session:
        try:
            await vault_service.initialize_vault(session, passphrase)
            await session.commit()
        except vault_service.VaultAlreadyInitializedError:
            # Race or refresh — bounce to home.
            return RedirectResponse(url="/dashboard/", status_code=303)
        except Exception:
            log.exception("vault initialization failed")
            await session.rollback()
            return _render_setup(
                error="Could not initialize the vault. Try again, or check logs.",
                suggested="",
            )

    log.info("vault setup completed; redirecting to /dashboard/")
    return RedirectResponse(url="/dashboard/vault/", status_code=303)


# ── /dashboard/vault/ — smart index ───────────────────────────────────────


@router.get(
    "/dashboard/vault/",
    dependencies=[Depends(require_dashboard_token)],
    response_class=HTMLResponse,
    response_model=None,
)
async def vault_index_get(request: Request):
    """Single entry point: routes the owner to setup, unlock, or the list
    based on current state. List view lands in step 1.5; for now, an
    unlocked owner sees a placeholder.
    """
    async with async_session() as session:
        if not await vault_service.is_vault_initialized(session):
            return RedirectResponse(url="/dashboard/vault/setup", status_code=303)
        status = await vault_service.get_vault_status(session)

    sid = _unlock_session_id(request)
    if not vault_service.is_session_unlocked(sid):
        return RedirectResponse(url="/dashboard/vault/unlock", status_code=303)

    # Unlocked — placeholder until step 1.5 ships the real list.
    placeholder = (
        '<section class="atlas-section"><div class="atlas-panel atlas-panel--full">'
        f"<h3>Vault is unlocked</h3>"
        f'<p>Key ID <code>{html.escape(status.public_key_b64 or "")}</code>. '
        "Secret list and reveal flow ship in step 1.5.</p>"
        '<form method="post" action="/dashboard/vault/lock" style="margin-top:14px;">'
        '<button class="atlas-btn atlas-btn--ghost" type="submit">Lock vault</button>'
        "</form></div></section>"
    )
    return render_dashboard_shell(
        title="Vault",
        token="",
        active_page="vault",
        hero_kicker="Vault",
        hero_title="Vault is unlocked.",
        hero_subtitle=f"Key ID {status.public_key_b64 or ''}. Stays unlocked for 8 hours unless you lock it.",
        content_html=placeholder,
    )


# ── Unlock — GET ──────────────────────────────────────────────────────────


def _render_unlock(
    *,
    fingerprint: str | None = None,
    next_path: str = "/dashboard/",
    error: str | None = None,
) -> HTMLResponse:
    safe_next = _safe_next(next_path)
    error_html = (
        f'<div class="vault-setup-feedback" data-state="error">{html.escape(error)}</div>'
        if error
        else ""
    )
    content = _UNLOCK_TEMPLATE.safe_substitute(
        fingerprint=html.escape(fingerprint or ""),
        next_path=html.escape(safe_next),
        error_html=error_html,
    )
    return render_dashboard_shell(
        title="Unlock vault",
        token="",
        active_page="vault",
        hero_kicker="Vault",
        hero_title="Unlock the vault.",
        hero_subtitle="Enter your passphrase to decrypt the vault into memory for this session.",
        content_html=content,
    )


@router.get(
    "/dashboard/vault/unlock",
    dependencies=[Depends(require_dashboard_token)],
    response_class=HTMLResponse,
    response_model=None,
)
async def vault_unlock_get(
    request: Request,
    next: str = Query(default="/dashboard/vault/"),  # noqa: A002 (shadow ok)
):
    async with async_session() as session:
        if not await vault_service.is_vault_initialized(session):
            return RedirectResponse(url="/dashboard/vault/setup", status_code=303)
        status = await vault_service.get_vault_status(session)

    sid = _unlock_session_id(request)
    if vault_service.is_session_unlocked(sid):
        return RedirectResponse(url=_safe_next(next), status_code=303)

    return _render_unlock(fingerprint=status.public_key_b64, next_path=next)


# ── Unlock — POST ─────────────────────────────────────────────────────────


@router.post(
    "/dashboard/vault/unlock",
    dependencies=[Depends(require_dashboard_token)],
    response_class=HTMLResponse,
    response_model=None,
)
async def vault_unlock_post(
    request: Request,
    passphrase: str = Form(default=""),
    next: str = Form(default="/dashboard/vault/"),  # noqa: A002
):
    if not passphrase:
        async with async_session() as session:
            status = await vault_service.get_vault_status(session)
        return _render_unlock(
            fingerprint=status.public_key_b64,
            next_path=next,
            error="Passphrase required.",
        )

    sid = _unlock_session_id(request)
    async with async_session() as session:
        try:
            await vault_service.unlock_vault_for_session(
                session,
                unlock_session_id=sid,
                passphrase=passphrase,
                ip=_client_ip(request),
                device_fingerprint=_device_fingerprint(request),
            )
            await session.commit()
        except vault_service.VaultNotInitializedError:
            return RedirectResponse(url="/dashboard/vault/setup", status_code=303)
        except vault_crypto.VaultPassphraseError:
            await session.rollback()
            status = await vault_service.get_vault_status(session)
            return _render_unlock(
                fingerprint=status.public_key_b64,
                next_path=next,
                error="Passphrase did not unlock the vault. Try again.",
            )
        except Exception:
            log.exception("vault unlock failed")
            await session.rollback()
            status = await vault_service.get_vault_status(session)
            return _render_unlock(
                fingerprint=status.public_key_b64,
                next_path=next,
                error="Could not unlock right now. Try again.",
            )

    return RedirectResponse(url=_safe_next(next), status_code=303)


# ── Lock ──────────────────────────────────────────────────────────────────


@router.post(
    "/dashboard/vault/lock",
    dependencies=[Depends(require_dashboard_token)],
    response_class=HTMLResponse,
    response_model=None,
)
async def vault_lock_post(request: Request):
    sid = _unlock_session_id(request)
    async with async_session() as session:
        await vault_service.lock_vault_for_session(session, unlock_session_id=sid)
        await session.commit()
    return RedirectResponse(url="/dashboard/vault/", status_code=303)
