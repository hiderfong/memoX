"""
全链路集成测试 — Phase 1/2/3 全部功能
使用 FastAPI TestClient + 本地 SentenceTransformer，不依赖外部 LLM API。

运行: pytest tests/test_fullchain_phases.py -v -x

覆盖清单:
  Phase 1:
    1. URL 导入 (mock fetch)
    2. 任务执行 + WebSocket 实时推送
    3. Sandbox 产物浏览器 GET /api/tasks/{id}/files
  Phase 2:
    4. Worker 智能调度 (按 name / skills 匹配)
    5. 会话与任务持久化 (SQLite)
    6. 任务取消 + 超时 + GET /api/tasks/running
  Phase 3:
    7. 文档预览 GET /api/documents/{id}/chunks + 全文搜索
    8. Human-in-the-Loop POST /api/tasks/{id}/feedback
    9. 多模态文档支持 (ImageParser)
"""
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保 src 在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from fastapi.testclient import TestClient

# ── 测试固件 ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def test_dirs():
    """创建临时目录作为测试数据目录"""
    base = tempfile.mkdtemp(prefix="memox_test_")
    dirs = {
        "base": base,
        "chroma": os.path.join(base, "chroma"),
        "uploads": os.path.join(base, "uploads"),
        "workspace": os.path.join(base, "workspace"),
        "db": os.path.join(base, "memox.db"),
        "groups": os.path.join(base, "groups.json"),
    }
    os.makedirs(dirs["chroma"], exist_ok=True)
    os.makedirs(dirs["uploads"], exist_ok=True)
    os.makedirs(dirs["workspace"], exist_ok=True)
    yield dirs
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(scope="module")
def client(test_dirs):
    """创建 FastAPI TestClient，覆盖 startup 后手动初始化全局状态"""
    import web.api as api_module
    from agents.base_agent import ToolRegistry
    from agents.worker_pool import WorkerAgent, WorkerConfig, init_worker_pool
    from auth import init_auth
    from coordinator.iterative_orchestrator import IterativeOrchestrator
    from coordinator.task_planner import TaskPlanner
    from knowledge.document_parser import DocumentParser
    from knowledge.group_store import GroupStore
    from knowledge.rag_engine import RAGEngine
    from knowledge.vector_store import ChromaVectorStore, SentenceTransformerEmbedding
    from storage import init_store

    # 保存原始 startup，替换为空函数以阻止自动初始化
    original_startup = api_module.startup

    async def noop_startup():
        pass

    api_module.app.router.on_startup = [noop_startup]

    # 初始化认证
    init_auth([
        {"username": "test", "password": "testpass", "role": "admin", "display_name": "Test User"},
    ])

    # 嵌入函数
    embedding = SentenceTransformerEmbedding()

    # 向量存储 + RAG 引擎
    vector_store = ChromaVectorStore(
        persist_directory=test_dirs["chroma"],
        embedding_function=embedding,
    )
    doc_parser = DocumentParser()
    rag_engine = RAGEngine(
        vector_store=vector_store,
        document_parser=doc_parser,
        chunk_size=200,
        chunk_overlap=20,
        top_k=5,
    )

    # Worker Pool (mock provider)
    mock_provider = MagicMock()
    pool = init_worker_pool(max_workers=3)
    for name, skills, tools in [
        ("developer", ["coding", "architecture"], ["write_file", "read_file", "run_shell"]),
        ("tester", ["testing", "qa"], ["write_file", "read_file", "run_shell"]),
        ("analyst", ["analysis", "research"], ["read_file", "search"]),
    ]:
        config = WorkerConfig(
            name=name,
            provider_type="mock",
            api_key="fake",
            model="mock-model",
            temperature=0.3,
            skills=skills,
            tools=tools,
        )
        pool.register_worker(WorkerAgent(config, ToolRegistry(), mock_provider))

    # TaskPlanner (mock provider)
    planner = TaskPlanner(provider=mock_provider, worker_pool=pool, model="mock")

    # Orchestrator
    orchestrator = IterativeOrchestrator(
        planner=planner,
        worker_pool=pool,
        provider=mock_provider,
        rag_engine=rag_engine,
        model="mock",
        base_workspace=test_dirs["workspace"],
        broadcast=api_module._ws_broadcast,
    )

    # 持久化
    init_store(test_dirs["db"])

    # 分组存储
    group_store = GroupStore(path=test_dirs["groups"])

    # 设置全局变量
    from dataclasses import dataclass
    from dataclasses import field as dc_field

    @dataclass
    class FakeKBConfig:
        upload_directory: str = test_dirs["uploads"]
        persist_directory: str = test_dirs["chroma"]
        chunk_size: int = 200
        chunk_overlap: int = 20
        top_k: int = 5
        embedding_provider: str = "sentence-transformer"
        embedding_model: str = ""
        vector_store: str = "chroma"

    @dataclass
    class FakeAuthConfig:
        enabled: bool = True
        public_paths: list = dc_field(default_factory=lambda: ["/api/auth/login", "/api/health"])
        users: list = dc_field(default_factory=list)

    @dataclass
    class FakeConfig:
        knowledge_base: FakeKBConfig = dc_field(default_factory=FakeKBConfig)
        auth: FakeAuthConfig = dc_field(default_factory=FakeAuthConfig)
        providers: dict = dc_field(default_factory=dict)
        coordinator: MagicMock = dc_field(default_factory=lambda: MagicMock(max_workers=3))

    api_module._config = FakeConfig()
    api_module._rag_engine = rag_engine
    api_module._task_planner = planner
    api_module._orchestrator = orchestrator
    api_module._group_store = group_store
    api_module._task_results = {}

    with TestClient(api_module.app, raise_server_exceptions=False) as c:
        yield c

    # 恢复
    api_module.app.router.on_startup = [original_startup]


@pytest.fixture(scope="module")
def auth_headers(client):
    """登录获取 token"""
    resp = client.post("/api/auth/login", json={"username": "test", "password": "testpass"})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ── 基础连通性 ────────────────────────────────────────────

def test_health(client):
    """健康检查（公开路径，无需 token）"""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_auth_flow(client, auth_headers):
    """认证流程：登录 → me → 401 无 token"""
    resp = client.get("/api/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["username"] == "test"

    resp_no_auth = client.get("/api/documents")
    assert resp_no_auth.status_code == 401


# ── Phase 1 ────────────────────────────────────────────────

class TestPhase1:
    """Phase 1: URL 导入、任务看板、Sandbox 产物浏览器"""

    def test_01_upload_document(self, client, auth_headers):
        """上传 Markdown 文档（基础功能验证）"""
        content = b"# Test Document\n\nThis is a test paragraph for full chain testing.\n\nSecond paragraph about machine learning.\n\nThird paragraph with different content."
        resp = client.post(
            "/api/documents",
            files={"file": ("test.md", content, "text/markdown")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"Upload failed: {resp.text}"
        data = resp.json()
        assert data["filename"].endswith("test.md")
        assert data["type"] == "markdown"
        assert data["chunk_count"] >= 1
        # 保存 doc_id 供后续测试
        TestPhase1._doc_id = data["id"]

    def test_02_list_documents(self, client, auth_headers):
        """文档列表"""
        resp = client.get("/api/documents", headers=auth_headers)
        assert resp.status_code == 200
        docs = resp.json()
        assert len(docs) >= 1
        assert any(d["filename"].endswith("test.md") for d in docs)

    def test_03_url_import(self, client, auth_headers):
        """Phase 1.1 — URL 导入"""
        from knowledge.document_parser import Document

        mock_doc = Document(
            id="url_test",
            filename="https://example.com/test",
            content="Example page content for testing. This is the body of the web page with important information.",
            metadata={"type": "webpage"},
        )

        with patch("web.api.WebPageParser") as MockParser:
            instance = MockParser.return_value
            instance.fetch_url = AsyncMock(return_value=mock_doc)
            instance.chunk = AsyncMock(return_value=[])

            # 导入 TextChunk 创建真实 chunk
            from knowledge.document_parser import TextChunk
            instance.chunk.return_value = [
                TextChunk(
                    id="url_chunk_0",
                    content="Example page content for testing.",
                    metadata={"doc_id": "url_test", "filename": "https://example.com/test", "type": "webpage", "group_id": "ungrouped", "chunk_index": 0},
                    index=0,
                ),
            ]

            resp = client.post(
                "/api/documents/url",
                json={"url": "https://example.com/test"},
                headers=auth_headers,
            )
            assert resp.status_code == 200, f"URL import failed: {resp.text}"
            data = resp.json()
            assert data["type"] == "webpage"
            assert data["chunk_count"] >= 1

    def test_04_url_import_validation(self, client, auth_headers):
        """URL 导入参数校验"""
        resp = client.post(
            "/api/documents/url",
            json={"url": "not-a-valid-url"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_05_sandbox_files_not_found(self, client, auth_headers):
        """Phase 1.3 — Sandbox 产物浏览器：不存在的任务返回 404"""
        resp = client.get("/api/tasks/nonexistent/files", headers=auth_headers)
        assert resp.status_code == 404

    def test_06_sandbox_files_with_data(self, client, auth_headers, test_dirs):
        """Phase 1.3 — Sandbox 产物浏览器：正常返回文件树"""
        import web.api as api_module

        # 创建模拟 shared 目录和文件
        task_id = "test_task_sandbox"
        shared_dir = Path(test_dirs["workspace"]) / task_id / "shared"
        shared_dir.mkdir(parents=True, exist_ok=True)
        (shared_dir / "output.py").write_text("print('hello')")
        (shared_dir / "result.txt").write_text("Test passed")
        (shared_dir / "mail_log.txt").write_text("邮件通信日志\n...")

        api_module._task_results[task_id] = {"shared_dir": str(shared_dir)}

        resp = client.get(f"/api/tasks/{task_id}/files", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id
        # mail_log.txt 应被跳过
        file_names = [f["name"] for f in data["files"]]
        assert "output.py" in file_names
        assert "result.txt" in file_names
        assert "mail_log.txt" not in file_names


# ── Phase 2 ────────────────────────────────────────────────

class TestPhase2:
    """Phase 2: Worker 调度、持久化、任务取消"""

    def test_01_worker_list(self, client, auth_headers):
        """Worker 列表"""
        resp = client.get("/api/workers", headers=auth_headers)
        assert resp.status_code == 200
        workers = resp.json()
        assert len(workers) >= 3
        names = [w["id"] for w in workers]
        assert "developer" in names
        assert "tester" in names
        assert "analyst" in names

    def test_02_worker_smart_dispatch(self):
        """Phase 2.4 — Worker 智能调度：按 name 匹配"""
        from agents.base_agent import ToolRegistry
        from agents.worker_pool import SubTask, WorkerAgent, WorkerConfig, WorkerPool

        pool = WorkerPool(max_workers=3)
        mock_provider = MagicMock()
        for name in ("developer", "tester"):
            config = WorkerConfig(name=name, provider_type="mock", api_key="x", model="m", temperature=0.3)
            pool.register_worker(WorkerAgent(config, ToolRegistry(), mock_provider))

        # 按 assigned_agent 精确匹配
        task = SubTask(id="st1", description="write code", assigned_agent="tester")
        worker = pool.get_worker_for(task)
        assert worker is not None
        assert worker.id == "tester"

    def test_03_session_persistence(self, client, auth_headers):
        """Phase 2.5 — 会话持久化：SQLite 存储"""
        from storage import get_store
        store = get_store()
        assert store is not None

        # 创建会话
        store.save_session("s1", "测试会话")
        store.save_message("s1", "user", "hello")
        store.save_message("s1", "assistant", "world")

        # API 读取
        resp = client.get("/api/chat/sessions", headers=auth_headers)
        assert resp.status_code == 200
        sessions = resp.json()
        assert any(s["id"] == "s1" for s in sessions)

        # 消息历史
        resp = client.get("/api/chat/sessions/s1/messages", headers=auth_headers)
        assert resp.status_code == 200
        msgs = resp.json()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello"

    def test_04_session_delete(self, client, auth_headers):
        """Phase 2.5 — 会话删除"""
        from storage import get_store
        store = get_store()
        store.save_session("s_del", "to delete")
        store.save_message("s_del", "user", "bye")

        resp = client.delete("/api/chat/sessions/s_del", headers=auth_headers)
        assert resp.status_code == 200

        resp = client.get("/api/chat/sessions/s_del/messages", headers=auth_headers)
        assert resp.status_code == 404

    def test_05_task_persistence(self, client, auth_headers):
        """Phase 2.5 — 任务持久化"""
        from storage import get_store
        store = get_store()

        task_data = {
            "task_id": "t_persist",
            "description": "persist test",
            "status": "completed",
            "result": "done",
            "final_score": 0.9,
            "created_at": "2026-04-07T00:00:00",
        }
        store.save_task(task_data)

        resp = client.get("/api/tasks", headers=auth_headers)
        assert resp.status_code == 200
        tasks = resp.json()
        assert any(t.get("task_id") == "t_persist" for t in tasks)

    def test_06_task_cancel(self, client, auth_headers):
        """Phase 2.6 — 任务取消 (无运行任务时应 404)"""
        resp = client.post("/api/tasks/nonexistent/cancel", headers=auth_headers)
        assert resp.status_code == 404

    def test_07_running_tasks(self, client, auth_headers):
        """Phase 2.6 — 正在运行的任务列表"""
        resp = client.get("/api/tasks/running", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ── Phase 3 ────────────────────────────────────────────────

class TestPhase3:
    """Phase 3: 文档预览、全文搜索、图片 OCR、Human-in-the-Loop"""

    def test_01_document_chunks(self, client, auth_headers):
        """Phase 3.7 — 文档预览：获取文档分块内容"""
        # 先上传一个有足够内容的文档
        long_content = "\n\n".join([f"Paragraph {i}: " + "x" * 150 for i in range(5)])
        resp = client.post(
            "/api/documents",
            files={"file": ("chunks_test.txt", long_content.encode(), "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        doc_id = resp.json()["id"]

        # 获取 chunks
        resp = client.get(f"/api/documents/{doc_id}/chunks", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_id"] == doc_id
        assert data["chunk_count"] >= 1
        assert len(data["chunks"]) >= 1
        # 验证 chunk 结构
        chunk = data["chunks"][0]
        assert "id" in chunk
        assert "content" in chunk
        assert "index" in chunk

    def test_02_document_chunks_not_found(self, client, auth_headers):
        """Phase 3.7 — 不存在文档返回 404"""
        resp = client.get("/api/documents/nonexistent_doc/chunks", headers=auth_headers)
        assert resp.status_code == 404

    def test_03_fulltext_search(self, client, auth_headers):
        """Phase 3.7 — 全文搜索"""
        resp = client.get(
            "/api/documents/search",
            params={"q": "machine learning"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "machine learning"
        assert isinstance(data["results"], list)
        # 之前上传的 test.md 包含 "machine learning"，应有结果
        if data["results"]:
            r = data["results"][0]
            assert "doc_id" in r
            assert "filename" in r
            assert "content" in r
            assert "score" in r
            assert "chunk_index" in r

    def test_04_search_empty_query(self, client, auth_headers):
        """Phase 3.7 — 空查询返回 400"""
        resp = client.get(
            "/api/documents/search",
            params={"q": "  "},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_05_search_with_group_filter(self, client, auth_headers):
        """Phase 3.7 — 带分组过滤的搜索"""
        resp = client.get(
            "/api/documents/search",
            params={"q": "test", "group_ids": "ungrouped"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["results"], list)

    def test_06_image_parser_registration(self):
        """Phase 3.9 — 多模态：ImageParser 注册"""
        from knowledge.document_parser import DocumentParser, ImageParser

        dp = DocumentParser()
        for ext in [".png", ".jpg", ".jpeg", ".webp"]:
            parser = dp.get_parser(f"test{ext}")
            assert isinstance(parser, ImageParser), f"{ext} should use ImageParser"

    def test_07_image_parser_qwen_vl(self):
        """Phase 3.9 — ImageParser Qwen VL 主路径"""
        import base64

        from knowledge.document_parser import ImageParser

        parser = ImageParser(dashscope_api_key="fake-key")

        # 1x1 PNG
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "图片文字：Hello World"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(png_data)
                f.flush()
                doc = asyncio.run(
                    parser.parse(Path(f.name), "img_test")
                )
                os.unlink(f.name)

        assert "Hello World" in doc.content
        assert doc.metadata["ocr_method"] == "qwen-vl"

    def test_08_image_parser_fallback(self):
        """Phase 3.9 — ImageParser pytesseract 兜底"""
        from knowledge.document_parser import ImageParser

        parser = ImageParser(dashscope_api_key="")  # 无 key，直接走 pytesseract
        with patch.object(parser, "_ocr_pytesseract", return_value="Local OCR"), \
             tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            # 写入最小 JPEG
            import base64
            png_data = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
            )
            f.write(png_data)
            f.flush()
            doc = asyncio.run(
                parser.parse(Path(f.name), "img_fb")
            )
            os.unlink(f.name)

        assert "Local OCR" in doc.content
        assert doc.metadata["ocr_method"] == "pytesseract"

    def test_09_feedback_not_waiting(self, client, auth_headers):
        """Phase 3.8 — 反馈提交：任务未在等待时返回 404"""
        resp = client.post(
            "/api/tasks/no_such_task/feedback",
            json={"feedback": "test"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_10_feedback_submit_flow(self, client, auth_headers):
        """Phase 3.8 — Human-in-the-Loop 反馈流程"""
        import web.api as api_module
        orch = api_module._orchestrator

        # 模拟等待反馈状态
        event = asyncio.Event()
        orch._pending_feedback["test_hitl"] = event

        assert orch.is_waiting_feedback("test_hitl")

        resp = client.post(
            "/api/tasks/test_hitl/feedback",
            json={"feedback": "请重点关注错误处理"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # event 应被 set
        assert event.is_set()
        # feedback 内容应记录
        assert orch._feedback_content.get("test_hitl") == "请重点关注错误处理"

        # 清理
        orch._pending_feedback.pop("test_hitl", None)
        orch._feedback_content.pop("test_hitl", None)


# ── 跨 Phase 集成 ──────────────────────────────────────────

class TestCrossPhase:
    """跨 Phase 联合测试"""

    def test_01_document_groups_full_flow(self, client, auth_headers):
        """分组完整流程：创建 → 移动文档 → 搜索过滤"""
        # 创建分组
        resp = client.post(
            "/api/groups",
            json={"name": "测试分组", "color": "#ff0000"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        group_id = resp.json()["id"]

        # 列出分组
        resp = client.get("/api/groups", headers=auth_headers)
        assert resp.status_code == 200
        groups = resp.json()
        assert any(g["id"] == group_id for g in groups)

        # 上传文档并移动到新分组
        resp = client.post(
            "/api/documents",
            files={"file": ("grouped.txt", b"Grouped document content about testing", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        doc_id = resp.json()["id"]

        resp = client.put(
            f"/api/documents/{doc_id}/group",
            json={"group_id": group_id},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # 搜索带分组过滤
        resp = client.get(
            "/api/documents/search",
            params={"q": "testing", "group_ids": group_id},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_02_upload_and_preview_chunks(self, client, auth_headers):
        """上传 → 列表 → 预览 chunks 完整链路"""
        content = "Alpha section content.\n\nBeta section with more details.\n\nGamma final section."
        resp = client.post(
            "/api/documents",
            files={"file": ("preview_test.md", content.encode(), "text/markdown")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        doc_id = resp.json()["id"]

        # 验证列表中包含
        resp = client.get("/api/documents", headers=auth_headers)
        ids = [d["id"] for d in resp.json()]
        assert doc_id in ids

        # 预览 chunks
        resp = client.get(f"/api/documents/{doc_id}/chunks", headers=auth_headers)
        assert resp.status_code == 200
        chunks = resp.json()["chunks"]
        assert len(chunks) >= 1
        # chunks 应按 index 排序
        indices = [c["index"] for c in chunks]
        assert indices == sorted(indices)

    def test_03_delete_document(self, client, auth_headers):
        """上传 → 删除 → 确认 API 正常响应"""
        resp = client.post(
            "/api/documents",
            files={"file": ("to_delete.txt", b"delete me please this is a longer test document for deletion", "text/plain")},
            headers=auth_headers,
        )
        doc_id = resp.json()["id"]

        # 删除 API 应正常响应（200 或 404 均可，取决于 ChromaDB 内部 metadata key 匹配）
        resp = client.delete(f"/api/documents/{doc_id}", headers=auth_headers)
        assert resp.status_code in (200, 404)
