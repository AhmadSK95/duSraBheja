"""Authentication helpers for the private API and dashboard."""

from __future__ import annotations

from secrets import compare_digest

from fastapi import Header, HTTPException, Query, Request, status

from src.config import settings


def dashboard_username() -> str:
    return (settings.dashboard_username or "ahmad").strip() or "ahmad"


def dashboard_password() -> str:
    return (settings.dashboard_password or settings.api_token or "").strip()


def dashboard_session_secret() -> str:
    return (
        settings.dashboard_session_secret
        or settings.api_token
        or settings.encryption_master_key
        or "brain-dashboard-dev-secret"
    ).strip()


def dashboard_cookie_secure() -> bool:
    if settings.dashboard_cookie_secure:
        return True
    return str(settings.app_base_url or "").startswith("https://")


def is_dashboard_session_authenticated(request: Request) -> bool:
    session = getattr(request, "session", {}) or {}
    return bool(session.get("dashboard_authenticated"))


def dashboard_token_matches(value: str | None) -> bool:
    if not settings.api_token:
        return False
    if not value:
        return False
    return compare_digest(value.strip(), settings.api_token)


def dashboard_credentials_match(*, username: str | None, password: str | None) -> bool:
    expected_password = dashboard_password()
    if not expected_password:
        return False
    provided_username = (username or "").strip()
    provided_password = (password or "").strip()
    return compare_digest(provided_username, dashboard_username()) and compare_digest(provided_password, expected_password)


def dashboard_request_is_authenticated(request: Request) -> bool:
    if is_dashboard_session_authenticated(request):
        return True
    auth_header = request.headers.get("authorization") or ""
    if auth_header.startswith("Bearer ") and dashboard_token_matches(auth_header.removeprefix("Bearer ").strip()):
        return True
    return dashboard_token_matches(request.query_params.get("token"))


async def require_api_token(authorization: str | None = Header(default=None)) -> None:
    if not settings.api_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API token is not configured",
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    provided = authorization.removeprefix("Bearer ").strip()
    if not compare_digest(provided, settings.api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")


async def require_dashboard_token(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    if is_dashboard_session_authenticated(request):
        return
    if authorization and authorization.startswith("Bearer "):
        await require_api_token(authorization)
        return
    if not settings.api_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API token is not configured",
        )
    if not token or not compare_digest(token.strip(), settings.api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid dashboard token")
