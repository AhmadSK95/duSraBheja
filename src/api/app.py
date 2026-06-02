"""FastAPI application for the private brain service."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from src.api.routes import router
from src.database import async_session
from src.lib.auth import (
    dashboard_cookie_secure,
    dashboard_request_is_authenticated,
    dashboard_session_secret,
)
from src.services import vault as vault_service

STATIC_DIR = Path(__file__).resolve().parent / "static"
_log = logging.getLogger("brain-api-middleware")

# Paths that must remain reachable when the vault is uninitialized, so the
# owner can actually log in + complete setup.
VAULT_SETUP_EXEMPT_PATHS = {
    "/dashboard/login",
    "/dashboard/logout",
    "/dashboard/vault/setup",
    "/dashboard/vault/setup/generate",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="duSraBheja API", lifespan=lifespan)


async def dashboard_login_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/dashboard") and path not in {"/dashboard/login", "/dashboard/logout"}:
        if not dashboard_request_is_authenticated(request):
            next_path = "/dashboard/library" if request.url.path == "/dashboard" else request.url.path
            if request.url.query:
                next_path = f"{next_path}?{request.url.query}"
            return RedirectResponse(
                url=f"/dashboard/login?next={quote(next_path, safe='/?=&')}",
                status_code=303,
            )
    return await call_next(request)


async def vault_setup_required_middleware(request: Request, call_next):
    """If the owner is authenticated to the dashboard and the vault hasn't
    been initialized yet, redirect every dashboard request to the setup
    page. Exempts the setup pages themselves and the login/logout actions.
    """
    path = request.url.path
    if not path.startswith("/dashboard"):
        return await call_next(request)
    if path in VAULT_SETUP_EXEMPT_PATHS:
        return await call_next(request)
    if not dashboard_request_is_authenticated(request):
        # Login middleware (which runs before this one) will handle the
        # redirect to login. Just pass through.
        return await call_next(request)
    try:
        async with async_session() as session:
            already = await vault_service.is_vault_initialized(session)
    except Exception:
        # If we can't talk to the DB to check, fail open — the route layer
        # will surface the real error. Logging it here so it's not silent.
        _log.exception("vault setup check failed; allowing request through")
        return await call_next(request)
    if not already:
        return RedirectResponse(url="/dashboard/vault/setup", status_code=303)
    return await call_next(request)


# Middleware order note: Starlette runs middleware outermost-first; the one
# added LAST is the outermost. We want SessionMiddleware to wrap everything
# (so request.session works), then dashboard_login_middleware, then the
# vault check innermost.
app.add_middleware(
    BaseHTTPMiddleware,
    dispatch=vault_setup_required_middleware,
)
app.add_middleware(
    BaseHTTPMiddleware,
    dispatch=dashboard_login_middleware,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=dashboard_session_secret(),
    session_cookie="brain_dashboard_session",
    same_site="lax",
    https_only=dashboard_cookie_secure(),
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


app.include_router(router)
