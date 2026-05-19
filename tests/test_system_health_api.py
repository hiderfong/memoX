"""System health endpoint tests."""

from __future__ import annotations

import contextlib
import importlib
from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient


class EmptyVectorStore:
    def list_documents(self, collection_name: str = "documents") -> list[dict]:
        return []

    def get_chunks_by_doc(self, doc_id: str, collection_name: str = "documents") -> list[dict]:
        return []


def _write_config(root: Path) -> Path:
    data = root / "data"
    for name in ["chroma", "uploads", "skills"]:
        (data / name).mkdir(parents=True, exist_ok=True)
    (root / "workspace").mkdir()
    config = {
        "app": {"workspace": str(root / "workspace")},
        "knowledge_base": {
            "persist_directory": str(data / "chroma"),
            "upload_directory": str(data / "uploads"),
            "skills_dir": str(data / "skills"),
            "embedding_provider": "hash",
            "embedding_model": "hash-test",
            "hybrid_search": {
                "enabled": True,
                "bm25_persist_path": str(data / "bm25_index.pkl"),
            },
            "manifest_path": str(data / "documents_manifest.json"),
        },
        "auth": {
            "enabled": True,
            "public_paths": ["/api/auth/login", "/api/health"],
            "users": [
                {"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"},
                {"username": "user", "password": "pw", "role": "user", "display_name": "User"},
            ],
        },
    }
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


def test_system_health_requires_admin_and_reports_readiness(monkeypatch, tmp_path: Path) -> None:
    from src.web import api as api_module

    init_auth = importlib.import_module("auth").init_auth
    Config = importlib.import_module("config").Config
    BM25Indexer = importlib.import_module("knowledge.bm25_indexer").BM25Indexer

    config_path = _write_config(tmp_path)
    monkeypatch.setenv("MEMOX_CONFIG_PATH", str(config_path))
    original_lifespan = api_module.app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def noop_lifespan(app):
        yield

    api_module.app.router.lifespan_context = noop_lifespan
    api_module._config = Config.from_yaml(config_path)
    api_module._rag_engine = SimpleNamespace(
        vector_store=EmptyVectorStore(),
        _hybrid_retriever=SimpleNamespace(bm25_indexer=BM25Indexer(tmp_path / "data" / "bm25_index.pkl")),
    )
    auth = init_auth(
        [
            {"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"},
            {"username": "user", "password": "pw", "role": "user", "display_name": "User"},
        ],
        app_state=api_module.app.state,
    )
    admin_token = auth.login("admin", "pw")
    user_token = auth.login("user", "pw")

    try:
        with TestClient(api_module.app, raise_server_exceptions=False) as client:
            forbidden = client.get("/api/system/health", headers={"Authorization": f"Bearer {user_token}"})
            assert forbidden.status_code == 403

            response = client.get("/api/system/health", headers={"Authorization": f"Bearer {admin_token}"})
            assert response.status_code == 200
            payload = response.json()
    finally:
        api_module.app.router.lifespan_context = original_lifespan
        api_module._config = None
        api_module._rag_engine = None

    assert payload["ok"] is True
    assert payload["status"] == "warning"
    assert payload["runtime"]["config_loaded"] is True
    assert payload["runtime"]["rag_engine_loaded"] is True
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["config"]["status"] == "ok"
    assert checks["index_consistency"]["status"] == "ok"
    assert checks["sqlite"]["status"] == "warning"
    assert checks["disk"]["status"] == "ok"
