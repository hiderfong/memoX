"""Authentication security regression tests."""

from __future__ import annotations

import contextlib
import importlib

import pytest
from fastapi.testclient import TestClient

from src.auth import AuthManager, AuthRateLimitError


def test_failed_logins_lock_identity_per_client_and_reset_after_success() -> None:
    now = 1_000.0
    auth = AuthManager(now_fn=lambda: now)
    auth.LOGIN_FAILURE_LIMIT = 3
    auth.LOGIN_LOCK_SECONDS = 60
    auth.add_user("admin", "correct-password", role="admin")

    assert auth.login("admin", "wrong", client_key="client-a") is None
    assert auth.login("admin", "wrong", client_key="client-a") is None
    assert auth.login("admin", "wrong", client_key="client-a") is None

    with pytest.raises(AuthRateLimitError) as exc:
        auth.login("admin", "correct-password", client_key="client-a")
    assert exc.value.retry_after_seconds == 60

    token_from_other_client = auth.login("admin", "correct-password", client_key="client-b")
    assert token_from_other_client

    now += 61
    token = auth.login("admin", "correct-password", client_key="client-a")
    assert token
    assert auth.validate_token(token)["username"] == "admin"

    assert auth.login("admin", "wrong", client_key="client-a") is None
    assert auth.login("admin", "correct-password", client_key="client-a")


def test_tokens_expire_and_logout_revokes_token() -> None:
    now = 10_000.0
    auth = AuthManager(now_fn=lambda: now)
    auth.TOKEN_TTL = 30
    auth.add_user("admin", "pw", role="admin")

    token = auth.login("admin", "pw", client_key="test")
    assert auth.validate_token(token) is not None

    auth.logout(token)
    assert auth.validate_token(token) is None

    token = auth.login("admin", "pw", client_key="test")
    now += 31
    assert auth.validate_token(token) is None
    assert auth.active_token_count() == 0


def test_login_endpoint_returns_retry_after_when_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.web import api as api_module

    auth_module = importlib.import_module("auth")
    original_lifespan = api_module.app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def noop_lifespan(app):
        yield

    api_module.app.router.lifespan_context = noop_lifespan
    auth = auth_module.init_auth(
        [{"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"}],
    )
    auth.LOGIN_FAILURE_LIMIT = 2
    auth.LOGIN_LOCK_SECONDS = 60
    monkeypatch.setattr(api_module.app.state, "_auth_manager", auth, raising=False)

    try:
        with TestClient(api_module.app, raise_server_exceptions=False) as client:
            first = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
            second = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
            locked = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})

        assert first.status_code == 401
        assert second.status_code == 401
        assert locked.status_code == 429
        assert locked.headers["retry-after"] == "60"
        assert "登录失败次数过多" in locked.json()["detail"]
    finally:
        api_module.app.router.lifespan_context = original_lifespan
        api_module.app.dependency_overrides.clear()
