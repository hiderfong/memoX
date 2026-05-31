"""API authorization boundary regression tests."""

from __future__ import annotations

import contextlib
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def permission_client(monkeypatch: pytest.MonkeyPatch):
    from src.web import api as api_module

    auth_module = importlib.import_module("auth")
    original_lifespan = api_module.app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def noop_lifespan(app):
        yield

    api_module.app.router.lifespan_context = noop_lifespan
    api_module.app.dependency_overrides.clear()
    auth = auth_module.init_auth(
        [
            {"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"},
            {"username": "user", "password": "pw", "role": "user", "display_name": "User"},
        ],
        monitor_token="monitor-token-" + ("x" * 32),
    )
    monkeypatch.setattr(api_module.app.state, "_auth_manager", auth, raising=False)

    with TestClient(api_module.app, raise_server_exceptions=False) as client:
        yield client, {
            "admin": {"Authorization": f"Bearer {auth.login('admin', 'pw')}"},
            "user": {"Authorization": f"Bearer {auth.login('user', 'pw')}"},
            "monitor": {"Authorization": "Bearer monitor-token-" + ("x" * 32)},
        }

    api_module.app.router.lifespan_context = original_lifespan
    api_module.app.dependency_overrides.clear()
    api_module._config = None
    api_module._rag_engine = None
    api_module._memory_manager = None
    api_module._memory_recall = None


def test_public_and_protected_auth_boundaries(permission_client) -> None:
    client, _headers = permission_client

    public_health = client.get("/api/health")
    protected_documents = client.get("/api/documents")
    protected_auth_me = client.get("/api/auth/me")
    protected_file = client.get("/api/files/missing.png")

    assert public_health.status_code == 200
    assert protected_documents.status_code == 401
    assert protected_auth_me.status_code == 401
    assert protected_file.status_code == 401


def test_cors_preflight_uses_runtime_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import Config
    from src.web import api as api_module

    cfg = Config._from_dict(
        {
            "app": {},
            "server": {"cors_origins": ["https://console.example.com"]},
            "coordinator": {},
            "providers": {},
            "worker_templates": {},
            "knowledge_base": {},
            "auth": {"enabled": True, "users": [{"username": "admin", "password": "pw"}]},
        }
    )
    original_lifespan = api_module.app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def noop_lifespan(app):
        yield

    api_module.app.router.lifespan_context = noop_lifespan
    monkeypatch.setattr(api_module, "_config", cfg)
    try:
        with TestClient(api_module.app, raise_server_exceptions=False) as client:
            allowed = client.options(
                "/api/health",
                headers={
                    "Origin": "https://console.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            denied = client.options(
                "/api/health",
                headers={
                    "Origin": "https://unexpected.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
    finally:
        api_module.app.router.lifespan_context = original_lifespan

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://console.example.com"
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


@pytest.mark.asyncio
async def test_disabled_auth_allows_middleware_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from src.config import Config
    from src.web import api as api_module

    cfg = Config._from_dict(
        {
            "app": {},
            "server": {},
            "coordinator": {},
            "providers": {},
            "worker_templates": {},
            "knowledge_base": {},
            "auth": {"enabled": False, "users": []},
        }
    )
    monkeypatch.setattr(api_module, "_config", cfg)
    called = False

    async def call_next(request: Request) -> JSONResponse:
        nonlocal called
        called = True
        return JSONResponse({"ok": True}, status_code=204)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/protected",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
        }
    )
    response = await api_module.auth_middleware(request, call_next)

    assert called is True
    assert response.status_code == 204


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("DELETE", "/api/documents/doc-1", None),
        ("PUT", "/api/documents/doc-1/group", {"group_id": "group-1"}),
        ("PUT", "/api/groups/group-1", {"name": "Updated"}),
        ("DELETE", "/api/groups/group-1", None),
        (
            "POST",
            "/api/workers",
            {
                "name": "worker_1",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "skills": [],
                "tools": [],
            },
        ),
        (
            "PUT",
            "/api/workers/worker_1/config",
            {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "skills": [],
                "tools": [],
            },
        ),
        ("DELETE", "/api/workers/worker_1", None),
        ("DELETE", "/api/workers/worker_1/logs", None),
        ("POST", "/api/skills/install", {"source_url": "https://github.com/example/repo", "name": "example"}),
        ("POST", "/api/skills/rebuild-embeddings", None),
        ("POST", "/api/skills/lint", {"action": "upsert", "name": "example"}),
        ("DELETE", "/api/skills/example", None),
        ("POST", "/api/scheduled-tasks", {"description": "daily", "cron": "0 9 * * *"}),
        ("PATCH", "/api/scheduled-tasks/task-1", {"description": "updated"}),
        ("DELETE", "/api/scheduled-tasks/task-1", None),
        ("PATCH", "/api/memory/config", {"enabled": False}),
        ("GET", "/api/system/health", None),
        ("GET", "/api/system/tool-policy", None),
        (
            "PUT",
            "/api/system/tool-policy",
            {
                "network": {"allow_internal_hosts": []},
                "database": {
                    "default_access_mode": "read_only",
                    "allow_raw_connection_strings": True,
                    "allow_write": True,
                    "allow_ddl": False,
                    "allow_multiple_statements": False,
                    "max_result_rows": 200,
                    "data_sources": [],
                },
            },
        ),
        ("POST", "/api/system/maintenance/backup?force=true", None),
        ("POST", "/api/system/maintenance/lifecycle?dry_run=true", None),
    ],
)
def test_regular_user_cannot_access_admin_operations(
    permission_client,
    method: str,
    path: str,
    json_body: dict | None,
) -> None:
    client, headers = permission_client

    response = client.request(method, path, headers=headers["user"], json=json_body)

    assert response.status_code == 403


def test_monitor_token_can_only_read_monitor_endpoints(permission_client) -> None:
    client, headers = permission_client
    monitor_headers = headers["monitor"]

    public_health = client.get("/api/health", headers=monitor_headers)
    media_status = client.get("/api/videos/jobs/status", headers=monitor_headers)
    events = client.get("/api/system/events?status=warning&limit=5", headers=monitor_headers)
    tool_audit = client.get("/api/system/tool-audit?status=rejected&limit=5", headers=monitor_headers)

    assert public_health.status_code == 200
    assert media_status.status_code == 200
    assert events.status_code == 200
    assert tool_audit.status_code == 200

    assert client.get("/api/documents", headers=monitor_headers).status_code == 403
    assert client.get("/api/system/backups", headers=monitor_headers).status_code == 403
    assert client.get("/api/system/diagnostics/export", headers=monitor_headers).status_code == 403
    assert client.post("/api/system/maintenance/backup?force=true", headers=monitor_headers).status_code == 403
