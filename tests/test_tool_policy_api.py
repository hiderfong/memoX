from __future__ import annotations

import contextlib
import importlib
import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient


def _write_config(root: Path) -> Path:
    data = root / "data"
    (data / "chroma").mkdir(parents=True)
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
                {"username": "user", "password": "pw", "role": "user", "display_name": "User"},
            ],
        },
        "tool_policy": {
            "network": {"allow_internal_hosts": ["127.0.0.1:3000"]},
            "web": {
                "request_timeout_seconds": 12,
                "max_response_bytes": 1500000,
                "max_fetch_chars": 12000,
                "max_search_results": 8,
            },
            "playwright_crawler": {
                "max_concurrency": 2,
                "queue_timeout_seconds": 10,
                "total_timeout_seconds": 45,
                "navigation_timeout_ms": 30000,
                "selector_timeout_ms": 10000,
                "idle_wait_ms": 2000,
                "max_pages": 1,
                "max_response_bytes": 5000000,
                "max_output_chars": 8000,
            },
            "database": {
                "default_access_mode": "read_only",
                "allow_raw_connection_strings": True,
                "allow_write": True,
                "allow_ddl": False,
                "allow_multiple_statements": False,
                "max_result_rows": 200,
                "data_sources": {
                    "analytics": "postgresql://alice:secret@db.internal:5432/app?sslmode=require",
                    "demo": "${DEMO_DATABASE_URL}",
                },
            },
        },
    }
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return config_path


def test_tool_policy_api_redacts_persists_and_applies_runtime(monkeypatch, tmp_path: Path) -> None:
    from src.web import api as api_module

    auth_module = importlib.import_module("auth")
    config_module = importlib.import_module("config")
    src_config_module = importlib.import_module("src.config")
    storage = importlib.import_module("storage")
    persistence_module = importlib.import_module("storage.persistence")

    config_path = _write_config(tmp_path)
    monkeypatch.setenv("MEMOX_CONFIG_PATH", str(config_path))
    original_lifespan = api_module.app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def noop_lifespan(app):
        yield

    api_module.app.router.lifespan_context = noop_lifespan
    store = storage.init_store(tmp_path / "data" / "memox.db")
    api_module._config = config_module.Config.from_yaml(config_path)
    config_module._config = api_module._config
    auth = auth_module.init_auth(
        [
            {"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"},
            {"username": "user", "password": "pw", "role": "user", "display_name": "User"},
        ],
        app_state=api_module.app.state,
    )
    admin_headers = {"Authorization": f"Bearer {auth.login('admin', 'pw')}"}
    user_headers = {"Authorization": f"Bearer {auth.login('user', 'pw')}"}

    try:
        with TestClient(api_module.app, raise_server_exceptions=False) as client:
            forbidden = client.get("/api/system/tool-policy", headers=user_headers)
            assert forbidden.status_code == 403

            current = client.get("/api/system/tool-policy", headers=admin_headers)
            assert current.status_code == 200
            payload = current.json()
            serialized = json.dumps(payload, ensure_ascii=False)

            assert payload["network"]["allow_internal_hosts"] == ["127.0.0.1:3000"]
            assert payload["web"]["request_timeout_seconds"] == 12
            assert payload["web"]["max_response_bytes"] == 1500000
            assert payload["web"]["max_fetch_chars"] == 12000
            assert payload["web"]["max_search_results"] == 8
            assert payload["playwright_crawler"]["max_concurrency"] == 2
            assert payload["playwright_crawler"]["max_response_bytes"] == 5000000
            assert payload["database"]["data_sources"][0]["name"] == "analytics"
            assert payload["database"]["data_sources"][0]["redacted"] is True
            assert "secret" not in serialized
            assert "sslmode" not in serialized
            assert "${DEMO_DATABASE_URL}" in serialized

            update_payload = {
                "network": {"allow_internal_hosts": ["127.0.0.1:3000", "localhost:5173"]},
                "web": {
                    "request_timeout_seconds": 20,
                    "max_response_bytes": 2500000,
                    "max_fetch_chars": 30000,
                    "max_search_results": 12,
                },
                "playwright_crawler": {
                    "max_concurrency": 3,
                    "queue_timeout_seconds": 5,
                    "total_timeout_seconds": 60,
                    "navigation_timeout_ms": 25000,
                    "selector_timeout_ms": 8000,
                    "idle_wait_ms": 1000,
                    "max_pages": 2,
                    "max_response_bytes": 6000000,
                    "max_output_chars": 12000,
                },
                "database": {
                    "default_access_mode": "read_only",
                    "allow_raw_connection_strings": False,
                    "allow_write": True,
                    "allow_ddl": True,
                    "allow_multiple_statements": False,
                    "max_result_rows": 50,
                    "data_sources": [
                        {
                            "name": "analytics",
                            "connection_string": payload["database"]["data_sources"][0]["connection_string"],
                            "redacted": True,
                        },
                        {
                            "name": "local",
                            "connection_string": "sqlite:///data/local.db",
                            "redacted": False,
                        },
                    ],
                },
            }
            updated = client.put("/api/system/tool-policy", headers=admin_headers, json=update_payload)
            assert updated.status_code == 200
            updated_payload = updated.json()["tool_policy"]

            invalid = client.put(
                "/api/system/tool-policy",
                headers=admin_headers,
                json={
                    **update_payload,
                    "network": {"allow_internal_hosts": ["http://127.0.0.1/private"]},
                },
            )
            assert invalid.status_code == 400

        persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert persisted["tool_policy"]["network"]["allow_internal_hosts"] == ["127.0.0.1:3000", "localhost:5173"]
        assert persisted["tool_policy"]["web"]["request_timeout_seconds"] == 20
        assert persisted["tool_policy"]["web"]["max_response_bytes"] == 2500000
        assert persisted["tool_policy"]["web"]["max_fetch_chars"] == 30000
        assert persisted["tool_policy"]["web"]["max_search_results"] == 12
        assert persisted["tool_policy"]["playwright_crawler"]["max_concurrency"] == 3
        assert persisted["tool_policy"]["playwright_crawler"]["max_pages"] == 2
        assert persisted["tool_policy"]["playwright_crawler"]["max_response_bytes"] == 6000000
        assert persisted["tool_policy"]["database"]["allow_raw_connection_strings"] is False
        assert persisted["tool_policy"]["database"]["allow_ddl"] is True
        assert persisted["tool_policy"]["database"]["max_result_rows"] == 50
        assert (
            persisted["tool_policy"]["database"]["data_sources"]["analytics"]
            == "postgresql://alice:secret@db.internal:5432/app?sslmode=require"
        )
        assert persisted["tool_policy"]["database"]["data_sources"]["local"] == "sqlite:///data/local.db"
        assert api_module._config.tool_policy.database.allow_raw_connection_strings is False
        assert api_module._config.tool_policy.database.allow_ddl is True
        assert api_module._config.tool_policy.playwright_crawler.max_concurrency == 3
        assert updated_payload["database"]["data_sources"][0]["redacted"] is True

        audit_events = store.list_audit_events(resource="tool_policy", action="update")
        assert audit_events[0]["username"] == "admin"
        assert "secret" not in json.dumps(audit_events[0]["details"], ensure_ascii=False)
    finally:
        api_module.app.router.lifespan_context = original_lifespan
        api_module._config = None
        config_module._config = None
        src_config_module._config = None
        store.close()
        persistence_module._store = None
