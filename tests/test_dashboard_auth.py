from fastapi.testclient import TestClient
from starlette.requests import Request

from src.api.app import app
from src.config import settings
from src.lib.auth import is_dashboard_session_authenticated


def _request(scope_overrides: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/dashboard/atlas",
        "headers": [],
    }
    if scope_overrides:
        scope.update(scope_overrides)
    return Request(scope)


def test_dashboard_session_auth_false_without_session_scope() -> None:
    request = _request()
    assert is_dashboard_session_authenticated(request) is False


def test_dashboard_session_auth_true_with_authenticated_session() -> None:
    request = _request({"session": {"dashboard_authenticated": True}})
    assert is_dashboard_session_authenticated(request) is True


def test_dashboard_atlas_redirects_to_login_instead_of_crashing() -> None:
    client = TestClient(app)
    response = client.get("/dashboard/atlas", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/dashboard/login")


def test_dashboard_root_redirects_to_library() -> None:
    client = TestClient(app)
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/dashboard/login?next=/dashboard/library")


def test_dashboard_login_sets_session_cookie(monkeypatch) -> None:
    monkeypatch.setattr(settings, "dashboard_username", "ahmad")
    monkeypatch.setattr(settings, "dashboard_password", "super-secret-password")
    client = TestClient(app)
    response = client.post(
        "/dashboard/login",
        data={
            "username": "ahmad",
            "password": "super-secret-password",
            "next": "/dashboard/atlas",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "brain_dashboard_session=" in response.headers.get("set-cookie", "")
