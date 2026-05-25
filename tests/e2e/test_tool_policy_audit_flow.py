from __future__ import annotations

import contextlib
import importlib
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


def _write_config(root: Path) -> tuple[Path, Path]:
    data = root / "data"
    for name in ["chroma", "uploads", "skills"]:
        (data / name).mkdir(parents=True, exist_ok=True)
    (root / "workspace").mkdir()
    local_db = data / "local.db"
    config = {
        "app": {"workspace": str(root / "workspace")},
        "providers": {},
        "worker_templates": {},
        "knowledge_base": {
            "persist_directory": str(data / "chroma"),
            "upload_directory": str(data / "uploads"),
            "skills_dir": str(data / "skills"),
            "embedding_provider": "hash",
            "embedding_model": "hash-test",
        },
        "auth": {
            "enabled": True,
            "public_paths": ["/api/auth/login", "/api/health"],
            "users": [
                {"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"},
            ],
        },
        "tool_policy": {
            "network": {"allow_internal_hosts": ["127.0.0.1:3000"]},
            "database": {
                "default_access_mode": "read_only",
                "allow_raw_connection_strings": True,
                "allow_write": True,
                "allow_ddl": False,
                "allow_multiple_statements": False,
                "max_result_rows": 200,
                "data_sources": {"local": f"sqlite:///{local_db}"},
            },
        },
    }
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return config_path, local_db


@pytest.mark.asyncio
async def test_tool_policy_update_affects_runtime_tools_and_audit_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from src.web import api as api_module

    auth_module = importlib.import_module("auth")
    config_module = importlib.import_module("config")
    src_config_module = importlib.import_module("src.config")
    storage = importlib.import_module("storage")
    persistence_module = importlib.import_module("storage.persistence")
    from agents.base_agent import ToolRegistry
    from tools.database import DatabaseQueryTool

    config_path, local_db = _write_config(tmp_path)
    monkeypatch.setenv("MEMOX_CONFIG_PATH", str(config_path))
    original_lifespan = api_module.app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def noop_lifespan(app):
        yield

    api_module.app.router.lifespan_context = noop_lifespan
    store = storage.init_store(tmp_path / "data" / "memox.db")
    api_module._config = config_module.Config.from_yaml(config_path)
    config_module._config = api_module._config
    src_config_module._config = src_config_module.Config.from_yaml(config_path)
    auth = auth_module.init_auth(
        [{"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"}],
        app_state=api_module.app.state,
    )
    admin_headers = {"Authorization": f"Bearer {auth.login('admin', 'pw')}"}

    try:
        with TestClient(api_module.app, raise_server_exceptions=False) as client:
            current = client.get("/api/system/tool-policy", headers=admin_headers)
            assert current.status_code == 200
            assert current.json()["database"]["allow_raw_connection_strings"] is True

            update = client.put(
                "/api/system/tool-policy",
                headers=admin_headers,
                json={
                    "network": {"allow_internal_hosts": ["127.0.0.1:3000", "localhost:5173"]},
                    "database": {
                        "default_access_mode": "read_only",
                        "allow_raw_connection_strings": False,
                        "allow_write": True,
                        "allow_ddl": False,
                        "allow_multiple_statements": False,
                        "max_result_rows": 25,
                        "data_sources": [
                            {
                                "name": "local",
                                "connection_string": f"sqlite:///{local_db}",
                                "redacted": False,
                            },
                        ],
                    },
                },
            )
            assert update.status_code == 200
            updated_policy = update.json()["tool_policy"]
            assert updated_policy["database"]["allow_raw_connection_strings"] is False
            assert updated_policy["database"]["max_result_rows"] == 25

            registry = ToolRegistry(audit_context={"worker_id": "policy-e2e", "task_id": "task-policy-e2e"})
            registry.register(DatabaseQueryTool())

            rejected = await registry.execute(
                "database_query",
                {"connection_string": "sqlite:///:memory:", "query": "SELECT 1 AS value"},
            )
            assert rejected.startswith("Database query rejected:")
            assert "Raw connection strings are disabled" in rejected

            success_text = await registry.execute(
                "database_query",
                {"data_source": "local", "query": "SELECT 42 AS answer", "max_rows": 1000},
            )
            success = json.loads(success_text)
            assert success["columns"] == ["answer"]
            assert success["rows"] == [[42]]
            assert success["row_count"] == 1
            assert success["truncated"] is False

            audit = client.get(
                "/api/system/tool-audit",
                headers=admin_headers,
                params={
                    "tool_name": "database_query",
                    "worker_id": "policy-e2e",
                    "task_id": "task-policy-e2e",
                    "limit": 10,
                },
            )
            assert audit.status_code == 200
            audit_payload = audit.json()
            assert audit_payload["total"] == 2
            assert audit_payload["summary"] == {"success": 1, "rejected": 1, "error": 0}
            assert {event["details"]["status"] for event in audit_payload["events"]} == {"success", "rejected"}

            rejected_audit = client.get(
                "/api/system/tool-audit",
                headers=admin_headers,
                params={
                    "tool_name": "database_query",
                    "status": "rejected",
                    "worker_id": "policy-e2e",
                    "task_id": "task-policy-e2e",
                },
            )
            assert rejected_audit.status_code == 200
            rejected_payload = rejected_audit.json()
            assert rejected_payload["total"] == 1
            assert "Raw connection strings are disabled" in rejected_payload["events"][0]["details"]["result"]["preview"]

            reloaded = client.get("/api/system/tool-policy", headers=admin_headers)
            assert reloaded.status_code == 200
            reloaded_policy = reloaded.json()
            assert reloaded_policy["network"]["allow_internal_hosts"] == ["127.0.0.1:3000", "localhost:5173"]
            assert reloaded_policy["database"]["allow_raw_connection_strings"] is False
            assert reloaded_policy["database"]["data_sources"] == [
                {"name": "local", "connection_string": f"sqlite:///{local_db}", "redacted": False}
            ]
    finally:
        api_module.app.router.lifespan_context = original_lifespan
        api_module._config = None
        config_module._config = None
        src_config_module._config = None
        store.close()
        persistence_module._store = None
