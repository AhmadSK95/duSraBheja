"""Dashboard vault routes — setup flow (Phase 1 step 1.3).

Three routes ship here:

- ``GET  /dashboard/vault/setup``      → render setup form
- ``POST /dashboard/vault/setup/generate`` → AJAX: return a fresh diceware
  suggestion for the regenerate button
- ``POST /dashboard/vault/setup``      → handle form submission

Unlock flow lands in step 1.4 in this same module.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from string import Template

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.api.dashboard_ui import render_dashboard_shell
from src.config import settings
from src.database import async_session
from src.lib import diceware
from src.lib.auth import require_dashboard_token
from src.services import vault as vault_service

router = APIRouter()
log = logging.getLogger("brain-vault-routes")

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
_SETUP_TEMPLATE = Template(
    (TEMPLATE_DIR / "dashboard_vault_setup.html").read_text(encoding="utf-8")
)

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
    # Defer to the home page — step 1.4 will add /dashboard/vault/unlock which
    # the new home will surface as the next action.
    return RedirectResponse(url="/dashboard/", status_code=303)
