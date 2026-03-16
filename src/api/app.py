"""FastAPI application for the private brain service."""

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from src.api.routes import router
from src.lib.auth import dashboard_cookie_secure, dashboard_request_is_authenticated, dashboard_session_secret

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="duSraBheja API", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=dashboard_session_secret(),
    session_cookie="brain_dashboard_session",
    same_site="lax",
    https_only=dashboard_cookie_secure(),
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def dashboard_login_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/dashboard") and path not in {"/dashboard/login", "/dashboard/logout"}:
        if not dashboard_request_is_authenticated(request):
            next_path = request.url.path
            if request.url.query:
                next_path = f"{next_path}?{request.url.query}"
            return RedirectResponse(
                url=f"/dashboard/login?next={quote(next_path, safe='/?=&')}",
                status_code=303,
            )
    return await call_next(request)


app.include_router(router)
