from starlette.requests import Request

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
