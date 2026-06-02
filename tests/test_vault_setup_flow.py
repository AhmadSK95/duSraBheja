"""End-to-end integration tests for the vault setup flow.

Exercises the real routes + middleware via FastAPI's TestClient, with the
DB session mocked out. Catches the failure modes most likely to bite in
production: redirect loops, missing exemptions, form-validation bugs,
sticky vault-initialized state across requests.

Does NOT test the SQL migration (covered by `alembic --sql` render +
the model definitions in test_vault_service.py). Run a real Postgres if
you want that coverage.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import src.api.app as app_module
import src.api.routes.vault as vault_routes
from src.api.app import app
from src.config import settings
from src.lib import vault_crypto
from src.models import VaultMaterial


# ── Fake session that the middleware AND route handlers share ─────────────


class _SharedDB:
    """Module-global pseudo-DB. One row at a time for the singleton vault."""

    def __init__(self) -> None:
        self.vault_row: VaultMaterial | None = None
        self.committed = False
        self.rolled_back = False


_DB = _SharedDB()


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """Just enough of an AsyncSession to satisfy is_vault_initialized,
    get_vault_material_record, and initialize_vault.
    """

    def __init__(self, shared: _SharedDB) -> None:
        self._shared = shared
        self._pending: VaultMaterial | None = None

    async def execute(self, _stmt):
        return _FakeResult(self._shared.vault_row)

    def add(self, instance) -> None:
        assert isinstance(instance, VaultMaterial)
        self._pending = instance

    async def flush(self) -> None:
        if self._pending is not None:
            self._shared.vault_row = self._pending
            self._pending = None

    async def commit(self) -> None:
        await self.flush()
        self._shared.committed = True

    async def rollback(self) -> None:
        self._pending = None
        self._shared.rolled_back = True


class _FakeSessionManager:
    """Async context manager that yields a FakeSession. Mirrors
    ``async with async_session() as session:`` semantics.
    """

    async def __aenter__(self) -> FakeSession:
        return FakeSession(_DB)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_shared_db() -> Generator[None, None, None]:
    """Fresh DB state per test."""
    _DB.vault_row = None
    _DB.committed = False
    _DB.rolled_back = False
    yield
    _DB.vault_row = None


@pytest.fixture(autouse=True)
def patch_async_session(monkeypatch) -> None:
    """Patch every call site that imports `async_session`."""
    monkeypatch.setattr(app_module, "async_session", lambda: _FakeSessionManager())
    monkeypatch.setattr(vault_routes, "async_session", lambda: _FakeSessionManager())


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch) -> None:
    """Replace production KDF params with weak ones — keeps each test
    well under a second. Real strength is tested in test_vault_crypto.
    """
    monkeypatch.setattr(
        vault_crypto,
        "DEFAULT_KDF_PARAMS",
        {"iterations": 1, "lanes": 2, "memory_cost_kib": 8192},
    )


_TEST_API_TOKEN = "test-token-for-vault-setup-flow"


@pytest.fixture
def authed_client(monkeypatch) -> Generator[TestClient, None, None]:
    """A TestClient authenticated via the Bearer-token escape hatch.

    Both `dashboard_request_is_authenticated` (used by middleware) and
    `require_dashboard_token` (used by route deps) accept a valid Bearer
    token, so this auth method exercises the full middleware chain plus
    the route-level dependency without depending on session cookies
    surviving the TestClient redirect machinery.
    """
    monkeypatch.setattr(settings, "api_token", _TEST_API_TOKEN)
    client = TestClient(
        app,
        headers={"Authorization": f"Bearer {_TEST_API_TOKEN}"},
    )
    yield client


@pytest.fixture
def anon_client() -> TestClient:
    return TestClient(app)


VALID_DICEWARE = "anchor-basket-cherry-dolphin-engine-feather-galaxy-harbor"


# ── Auth + middleware behavior ────────────────────────────────────────────


def test_unauthenticated_dashboard_redirects_to_login(anon_client: TestClient) -> None:
    response = anon_client.get("/dashboard/library", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/dashboard/login")


def test_authenticated_no_vault_redirects_to_setup(authed_client: TestClient) -> None:
    """The middleware should force the owner to setup on every dashboard page
    until the vault is initialized.
    """
    response = authed_client.get("/dashboard/library", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/vault/setup"


def test_login_page_remains_accessible_when_no_vault(anon_client: TestClient) -> None:
    """The vault middleware must NOT redirect /dashboard/login — otherwise no
    one can authenticate to set up the vault. Loop avoidance.
    """
    response = anon_client.get("/dashboard/login", follow_redirects=False)
    assert response.status_code == 200


def test_setup_page_is_exempt_from_vault_redirect(authed_client: TestClient) -> None:
    """Setup itself must not be redirected to setup."""
    response = authed_client.get("/dashboard/vault/setup", follow_redirects=False)
    assert response.status_code == 200


# ── GET setup ─────────────────────────────────────────────────────────────


def test_setup_get_renders_form_with_suggestion(authed_client: TestClient) -> None:
    response = authed_client.get("/dashboard/vault/setup")
    assert response.status_code == 200
    body = response.text
    assert "Vault setup" in body
    assert 'id="vault-passphrase"' in body
    assert 'name="acknowledged_backup"' in body
    # Suggestion should be present and dash-joined
    assert "-" in body  # crude but adequate
    assert "vault-suggestion" in body


def test_setup_get_redirects_when_already_initialized(authed_client: TestClient) -> None:
    # Seed vault state directly
    material = vault_crypto.initialize_vault("test-passphrase-for-setup")
    _DB.vault_row = VaultMaterial(
        salt=material.salt,
        kdf_params=dict(material.kdf_params),
        vault_public_key=material.vault_public_key,
        encrypted_vault_private_key=material.encrypted_vault_private_key,
        private_key_nonce=material.private_key_nonce,
        version=material.version,
    )
    response = authed_client.get("/dashboard/vault/setup", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/"


# ── Suggestion endpoint ───────────────────────────────────────────────────


def test_generate_endpoint_returns_diceware(authed_client: TestClient) -> None:
    response = authed_client.post("/dashboard/vault/setup/generate")
    assert response.status_code == 200
    body = response.json()
    assert "passphrase" in body
    tokens = body["passphrase"].split("-")
    assert len(tokens) >= 6
    assert all(t.isalpha() for t in tokens)


# ── POST setup — happy path ───────────────────────────────────────────────


def test_setup_post_valid_diceware_initializes_and_redirects(
    authed_client: TestClient,
) -> None:
    response = authed_client.post(
        "/dashboard/vault/setup",
        data={
            "passphrase": VALID_DICEWARE,
            "passphrase_confirm": VALID_DICEWARE,
            "acknowledged_backup": "yes",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/"
    assert _DB.vault_row is not None
    assert _DB.committed is True


def test_setup_post_valid_long_typed_passphrase_initializes(
    authed_client: TestClient,
) -> None:
    """An owner-typed long passphrase (not diceware) should also work if it
    meets the character-class entropy floor.
    """
    typed = "Tr0ub4dor&-3xampl-Vault-Setup-2026"
    response = authed_client.post(
        "/dashboard/vault/setup",
        data={
            "passphrase": typed,
            "passphrase_confirm": typed,
            "acknowledged_backup": "yes",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _DB.vault_row is not None


def test_setup_post_then_get_redirects_away(authed_client: TestClient) -> None:
    """After a successful setup, visiting the setup page redirects to home."""
    authed_client.post(
        "/dashboard/vault/setup",
        data={
            "passphrase": VALID_DICEWARE,
            "passphrase_confirm": VALID_DICEWARE,
            "acknowledged_backup": "yes",
        },
        follow_redirects=False,
    )
    response = authed_client.get("/dashboard/vault/setup", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/"


# ── POST setup — error paths ──────────────────────────────────────────────


def test_setup_post_mismatched_passphrases_re_renders(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/dashboard/vault/setup",
        data={
            "passphrase": VALID_DICEWARE,
            "passphrase_confirm": VALID_DICEWARE + "-tampered",
            "acknowledged_backup": "yes",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "do not match" in response.text
    assert _DB.vault_row is None


def test_setup_post_missing_acknowledgment_re_renders(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/dashboard/vault/setup",
        data={
            "passphrase": VALID_DICEWARE,
            "passphrase_confirm": VALID_DICEWARE,
            # acknowledged_backup omitted
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "stored the passphrase safely" in response.text
    assert _DB.vault_row is None


def test_setup_post_weak_short_passphrase_re_renders(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/dashboard/vault/setup",
        data={
            "passphrase": "short123",
            "passphrase_confirm": "short123",
            "acknowledged_backup": "yes",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Could be the "too short" or "too weak" branch — either is an error response
    assert _DB.vault_row is None


def test_setup_post_empty_fields_re_renders(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/dashboard/vault/setup",
        data={"passphrase": "", "passphrase_confirm": "", "acknowledged_backup": "yes"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "required" in response.text.lower()
    assert _DB.vault_row is None


# ── Vault-initialized state unlocks the rest of the dashboard ─────────────


def test_after_setup_other_dashboard_pages_no_longer_redirect_to_setup(
    authed_client: TestClient,
) -> None:
    """The vault middleware should stop redirecting once the singleton row
    exists.
    """
    authed_client.post(
        "/dashboard/vault/setup",
        data={
            "passphrase": VALID_DICEWARE,
            "passphrase_confirm": VALID_DICEWARE,
            "acknowledged_backup": "yes",
        },
        follow_redirects=False,
    )
    response = authed_client.get("/dashboard/library", follow_redirects=False)
    # Should NOT redirect to setup. Other middleware (dashboard auth,
    # dashboard page logic) may produce 200/303/etc. The thing we're testing
    # is the absence of the setup redirect.
    if response.status_code == 303:
        assert response.headers["location"] != "/dashboard/vault/setup"
