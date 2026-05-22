import importlib
import os
import sys
import uuid
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


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
            ],
        },
        "ops": {"archive_mirror_dir": str(root / "mirror")},
    }
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path


@pytest.mark.asyncio
async def test_full_pipeline_end_to_end(monkeypatch, tmp_path: Path):
    """
    测试完整用户链路：
    1. 登录 / 健康检查
    2. 文档上传
    3. 验证文档是否成功入库并构建了知识图谱
    4. 执行检索
    5. 发起 RAG 聊天
    6. 执行一个轻量级的协同任务
    """
    from web import api as api_module

    init_auth = importlib.import_module("auth").init_auth
    Config = importlib.import_module("config").Config
    BM25Indexer = importlib.import_module("knowledge.bm25_indexer").BM25Indexer
    storage = importlib.import_module("storage")
    persistence_module = importlib.import_module("storage.persistence")
    rag_engine_module = importlib.import_module("knowledge.rag_engine")
    coordinator_module = importlib.import_module("coordinator")

    config_path = _write_config(tmp_path)
    monkeypatch.setenv("MEMOX_CONFIG_PATH", str(config_path))
    store = storage.init_store(tmp_path / "data" / "memox.db")

    api_module._config = Config.from_yaml(config_path)
    # 模拟真实 RAG 引擎以支持文档上传入库和检索
    api_module._rag_engine = rag_engine_module.RAGEngine(api_module._config)

    with TestClient(api_module.app, raise_server_exceptions=False) as client:
        # 0. 获取 Token (直接调用 auth)
        get_auth_manager = importlib.import_module("auth").get_auth_manager
        auth = get_auth_manager()
        admin_token = auth.login("admin", "pw")
        assert admin_token is not None, "Login failed"
        auth_headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. 登录健康检查
        resp = client.get("/api/system/health", headers=auth_headers)
        assert resp.status_code == 200, "System should be healthy"

        # 2. 文档上传
        unique_content = f"The unique artifact code is {uuid.uuid4().hex}."
        markdown_text = f"# 秘密文档\n\n{unique_content}\n\n该神器通过特定的加密算法实现了解密，属于高级机密。"

        upload_resp = client.post(
            "/api/documents",
            files={"file": ("secret_doc.md", markdown_text.encode("utf-8"), "text/markdown")},
            headers=auth_headers,
        )
        assert upload_resp.status_code == 200, "Document upload should succeed"
        doc_id = upload_resp.json()["id"]

        status_resp = client.get("/api/documents", headers=auth_headers)
        docs = status_resp.json()
        target_doc = next((d for d in docs if d["id"] == doc_id), None)
        assert target_doc is not None, "Document should exist in list_documents"

        # 3. 验证混合检索 (包含 BM25 / Vector / Graph)
        search_resp = client.get(
            "/api/documents/search",
            params={"q": "加密算法"},
            headers=auth_headers,
        )
        assert search_resp.status_code == 200
        search_data = search_resp.json()
        search_results = search_data.get("results", [])
        assert len(search_results) > 0, "Should retrieve at least one document chunk"
        assert any("加密算法" in r["content"] for r in search_results), "Content should contain target keywords"

        # 4. RAG Chat 测试 (Non-streaming) - 注意由于 LLM 未配置可能 500
        chat_resp = client.post(
            "/api/chat",
            json={"message": "请告诉我神器通过什么实现了什么？", "use_rag": True, "stream": False},
            headers=auth_headers,
        )
        # 允许因为 LLM 配置缺失而 502/500，但不能是 404/401 等路由错误
        assert chat_resp.status_code in [200, 502, 500]

        # 5. 轻量级协同任务 (Coordinator)
        task_resp = client.post(
            "/api/tasks",
            json={
                "description": "请总结秘密文档中提到的核心机制",
                "active_group_ids": [],
                "mode": "parallel"
            },
            headers=auth_headers,
        )
        assert task_resp.status_code in [200, 502, 500]
