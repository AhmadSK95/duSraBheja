"""Authentication helpers for the private API and dashboard."""

from secrets import compare_digest

from fastapi import Header, HTTPException, Query, status

from src.config import settings


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
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
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
