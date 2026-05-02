"""Web API - FastAPI 服务"""

import asyncio
import contextlib
import json
import os
import re as _re
import sys
import uuid
from pathlib import Path

from loguru import logger

# 添加 src 目录到路径
_src_dir = Path(__file__).parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from typing import Annotated, Literal  # noqa: E402, I001

from fastapi import (  # noqa: E402
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from slowapi import Limiter  # noqa: E402
from slowapi.errors import RateLimitExceeded as RLError  # noqa: E402
from slowapi.util import get_remote_address  # noqa: E402

from agents.base_agent import ToolRegistry, create_provider  # noqa: E402
from agents.worker_pool import WorkerAgent, WorkerConfig, get_worker_pool, init_worker_pool  # noqa: E402
from auth import AuthUser, _get_auth_from_request, get_auth_manager, get_current_user, init_auth, require_role  # noqa: E402
from config import Config, load_config  # noqa: E402
from memory import MemoryManager, MemoryRecall, PreferenceLearner  # noqa: E402
from coordinator.iterative_orchestrator import IterativeOrchestrator  # noqa: E402
from coordinator.task_planner import TaskPlanner, init_task_planner  # noqa: E402
from knowledge import (  # noqa: E402
    DocumentInfo,
    RAGEngine,
    SearchResult,
    init_rag_engine,
)
from knowledge.document_parser import WebPageParser  # noqa: E402
from knowledge.group_store import UNGROUPED_ID, GroupStore  # noqa: E402
from knowledge.vector_store import (  # noqa: E402
    DashScopeEmbedding,
    OpenAIEmbedding,
    SentenceTransformerEmbedding,
)
from storage import get_store, init_store  # noqa: E402


def _audit_log(
    request: Request,
    user: AuthUser,
    action: str,
    resource: str,
    resource_id: str,
) -> None:
    """记录审计日志（失败不阻塞业务）"""
    try:
        store = get_store()
        if store:
            store.log_audit_event(
                action=action,
                resource=resource,
                resource_id=resource_id,
                username=user.username,
                user_role=user.role,
                ip_address=request.client.host if request.client else "",
            )
    except Exception:
        pass


# ==================== 配置 ====================

app = FastAPI(title="MemoX API", version="1.0.0")

# CORS 配置：允许本地开发端口和公网 IP 直接访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  "https://localhost:3000",
        "http://localhost:8080",  "https://localhost:8080",
        "http://127.0.0.1:3000", "https://127.0.0.1:3000",
        "http://127.0.0.1:8080", "https://127.0.0.1:8080",
        "http://23.236.66.33:8080", "https://23.236.66.33:8080",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

# ── Rate Limiter ────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter


@app.exception_handler(RLError)
async def rate_limit_handler(request, exc):
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"},
    )


UPLOADS_DIR = Path("data/uploads")

_config: Config | None = None
_rag_engine: RAGEngine | None = None
_task_planner: TaskPlanner | None = None
_orchestrator: IterativeOrchestrator | None = None
_memory_manager: MemoryManager | None = None
_memory_recall: MemoryRecall | None = None
_preference_learner: PreferenceLearner | None = None
_group_store: GroupStore | None = None
_task_results: dict[str, dict] = {}  # task_id -> full result dict (for later retrieval)
_ws_connections: set[WebSocket] = set()

# ==================== I2V 标记解析 ====================

_I2V_RE = _re.compile(r"\[\[I2V:\s*(.+?)\s*\|\s*(.+?)\]\]", flags=_re.DOTALL)


def parse_i2v_markers(text: str) -> list[tuple[str, str]]:
    """从 LLM 输出中抽取 [[I2V: <image_url> | <prompt>]] 对。

    过滤非 http(s) URL 和空 prompt。
    """
    out: list[tuple[str, str]] = []
    for url, prompt in _I2V_RE.findall(text):
        url = url.strip()
        prompt = prompt.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        if not prompt:
            continue
        out.append((url, prompt))
    return out


async def _ws_broadcast(data: dict) -> None:
    """广播消息到所有 WebSocket 连接"""
    message = json.dumps(data, ensure_ascii=False)
    dead: list[WebSocket] = []
    for ws in _ws_connections:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_connections.discard(ws)

# 精确公开路径白名单（仅这些路径无需 Token，不使用前缀匹配）
_PUBLIC_PATHS: set[str] = {
    "/api/auth/login",
    "/api/health",
    "/api/docs",
    "/api/openapi.json",
    "/docs",
    "/openapi.json",
}


# ==================== 认证中间件 ====================

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # 非 API 路径（前端静态文件、HTML 页面）直接放行
    if not path.startswith("/api/") and path != "/ws":
        return await call_next(request)

    # API 公开路径（登录、健康检查、文档）直接放行
    # 支持精确匹配和以 "/" 结尾的前缀匹配
    _is_public = any(
        (path == p) if not p.endswith("/") else path.startswith(p)
        for p in _PUBLIC_PATHS
    )
    if _is_public:
        return await call_next(request)

    # WebSocket 路径：token 通过 query param 传递
    if path == "/ws":
        token = request.query_params.get("token", "")
    else:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()

    auth = _get_auth_from_request(request)
    if not auth.validate_token(token):
        return JSONResponse(
            {"detail": "未登录或 Token 已过期，请重新登录"},
            status_code=401,
        )

    return await call_next(request)


# ==================== 模型 ====================

class ChatRequest(BaseModel):
    """聊天请求"""
    message: str
    session_id: str | None = None
    use_rag: bool = True
    stream: bool = True
    active_group_ids: list[str] | None = None
    worker_id: str | None = None  # 使用指定 Worker 的模型配置（不占用 Worker）


class URLRequest(BaseModel):
    """网页 URL 导入请求"""
    url: str


class TaskRequest(BaseModel):
    """任务请求"""
    description: str
    context: dict | None = None
    generate_suggestions: bool = True
    active_group_ids: list[str] | None = None
    timeout_seconds: int | None = None  # 任务超时（秒）


class DocumentResponse(BaseModel):
    """文档响应"""
    id: str
    filename: str
    type: str
    chunk_count: int
    created_at: str
    size: int
    group_id: str = "ungrouped"
    action: Literal["indexed", "skipped", "updated"] = "indexed"


class GroupCreate(BaseModel):
    name: str
    color: str = "#1890ff"


class GroupUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class MoveDocumentGroup(BaseModel):
    group_id: str


# ==================== 初始化 ====================

@app.on_event("startup")
async def startup():
    """启动时初始化"""
    global _config, _rag_engine, _task_planner

    # 加载配置
    _config = load_config()

    # 初始化认证
    if _config.auth.enabled:
        def _resolve(v: str) -> str:
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                return os.getenv(v[2:-1], "")
            return v
        init_auth(
            [
                {
                    "username": u.username,
                    "password": _resolve(u.password),
                    "role": u.role,
                    "display_name": u.display_name,
                }
                for u in _config.auth.users
            ],
            app_state=app.state,
        )
        _PUBLIC_PATHS.update(_config.auth.public_paths)
        logger.info(f"   - 认证已启用，{len(_config.auth.users)} 个用户")

    # CORS 已在创建 app 时配置

    # 根据配置创建 Embedding Function
    kb_config = _config.knowledge_base
    embedding_provider = kb_config.embedding_provider

    if embedding_provider == "dashscope":
        # 阿里云 DashScope
        dashscope_config = _config.providers.get("dashscope")
        if dashscope_config and dashscope_config.resolve_api_key():
            embedding_function = DashScopeEmbedding(
                api_key=dashscope_config.resolve_api_key(),
                model=kb_config.embedding_model or "text-embedding-v3"
            )
            logger.info(f"   - 使用 DashScope Embedding: {kb_config.embedding_model}")
        else:
            raise ValueError("DashScope API key not configured")
    elif embedding_provider == "openai":
        # OpenAI
        openai_config = _config.providers.get("openai")
        if openai_config and openai_config.resolve_api_key():
            embedding_function = OpenAIEmbedding(
                api_key=openai_config.resolve_api_key(),
                model=kb_config.embedding_model or "text-embedding-3-small"
            )
            logger.info(f"   - 使用 OpenAI Embedding: {kb_config.embedding_model}")
        else:
            raise ValueError("OpenAI API key not configured")
    else:
        # 默认本地 Sentence Transformer
        embedding_function = SentenceTransformerEmbedding(
            model_name=kb_config.embedding_model or "sentence-transformers/all-MiniLM-L6-v2"
        )
        logger.info(f"   - 使用本地 Embedding: {kb_config.embedding_model}")

    # DashScope config for image OCR
    dashscope_config = _config.providers.get("dashscope")
    dashscope_api_key = dashscope_config.resolve_api_key() if dashscope_config else ""
    dashscope_base_url = (dashscope_config.base_url if dashscope_config else "").replace("/api/v1", "/compatible-mode/v1")

    # 初始化 RAG 引擎
    hybrid_cfg = kb_config.hybrid_search
    _rag_engine = init_rag_engine(
        persist_directory=kb_config.persist_directory,
        embedding_function=embedding_function,
        chunk_size=kb_config.chunk_size,
        chunk_overlap=kb_config.chunk_overlap,
        top_k=hybrid_cfg.get("top_k", 5) if hybrid_cfg else 5,
        dashscope_api_key=dashscope_api_key,
        dashscope_base_url=dashscope_base_url,
        hybrid_search_enabled=hybrid_cfg.get("enabled", True) if hybrid_cfg else True,
        bm25_persist_path=hybrid_cfg.get("bm25_persist_path", "./data/bm25_index.pkl") if hybrid_cfg else "./data/bm25_index.pkl",
        chunk_strategy=hybrid_cfg.get("chunk_strategy", "size") if hybrid_cfg else "size",
        enable_graph=kb_config.enable_graph,
        graph_persist_path=kb_config.graph_persist_path,
        manifest_path=kb_config.manifest_path,
    )

    # 预热嵌入模型（避免首次请求时延迟）
    logger.info("   - 预热嵌入模型...")
    await _rag_engine.vector_store.embedding_function.embed(["预热模型"])

    # 初始化持久化存储（需要在 Worker 创建之前，以便 Worker 从 DB 恢复 token 用量）
    db_path = Path(_config.knowledge_base.persist_directory).parent / "memox.db"
    init_store(db_path)
    logger.info(f"   - 持久化存储: {db_path}")

    # 确保技能目录存在
    Path(_config.knowledge_base.skills_dir).mkdir(parents=True, exist_ok=True)

    # 初始化 Worker 池
    worker_pool = init_worker_pool(max_workers=_config.coordinator.max_workers)

    # 注册 Worker（从模板）
    for name, template in _config.worker_templates.items():
        provider_config = _config.providers.get(template.provider)
        if provider_config:
            worker_provider = create_provider(
                template.provider,
                provider_config.resolve_api_key(),
                base_url=provider_config.base_url,
                headers=provider_config.headers,
            )
        else:
            continue

        worker_config = WorkerConfig(
            name=name,
            provider_type=template.provider,
            api_key=provider_config.resolve_api_key() if provider_config else "",
            model=template.model,
            temperature=template.temperature,
            tools=template.tools,
            skills=template.skills,
            icon=template.icon,
            display_name=template.display_name,
        )

        worker_pool.register_worker(
            WorkerAgent(worker_config, ToolRegistry(), worker_provider)
        )

    # 初始化 Coordinator
    coordinator_provider_config = _config.providers.get(_config.coordinator.provider)
    if coordinator_provider_config:
        coordinator_provider = create_provider(
            _config.coordinator.provider,
            coordinator_provider_config.resolve_api_key(),
            base_url=coordinator_provider_config.base_url,
            headers=coordinator_provider_config.headers,
        )
        _task_planner = init_task_planner(
            coordinator_provider,
            worker_pool,
            _config.coordinator.model,
        )
        # 初始化迭代编排器
        global _orchestrator
        _orchestrator = IterativeOrchestrator(
            planner=_task_planner,
            worker_pool=worker_pool,
            provider=coordinator_provider,
            rag_engine=_rag_engine,
            model=_config.coordinator.model,
            temperature=_config.coordinator.temperature,
            base_workspace=str(Path(_config.knowledge_base.persist_directory).parent / "workspace"),
            broadcast=_ws_broadcast,
        )

    # 初始化分组存储
    global _group_store
    _group_store = GroupStore(path=str(Path(_config.knowledge_base.persist_directory).parent / "groups.json"))

    # 历史文档迁移：为无 group_id 的 chunk 补写 "ungrouped"
    migrated = _rag_engine.vector_store.migrate_add_group_id()
    if migrated > 0:
        logger.info(f"   - 迁移 {migrated} 个历史 chunk，补写 group_id=ungrouped")

    # 启动定时任务运行器（与 orchestrator、store 解耦）
    if _orchestrator:
        from scheduler import init_runner
        runner = init_runner(get_store(), _orchestrator)
        runner.start()

    # 注册优雅停机
    import atexit
    def _stop_scheduler():
        from scheduler import get_runner
        r = get_runner()
        if r:
            r.stop()
    atexit.register(_stop_scheduler)

    # 初始化文生图客户端
    img_cfg = _config.image_generation if _config else None
    if img_cfg and img_cfg.enabled:
        from imaging import init_image_client
        init_image_client(
            api_key=img_cfg.resolve_api_key(),
            model=img_cfg.model,
            default_size=img_cfg.default_size,
        )

    # 初始化文生视频客户端
    vid_cfg = _config.video_generation if _config else None
    if vid_cfg and vid_cfg.enabled:
        from imaging import init_video_client
        init_video_client(
            api_key=vid_cfg.resolve_api_key(),
            model=vid_cfg.model,
            default_resolution=vid_cfg.default_resolution,
            default_ratio=vid_cfg.default_ratio,
            default_duration=vid_cfg.default_duration,
        )

    # 初始化图生视频客户端
    i2v_cfg = _config.image_to_video if _config else None
    if i2v_cfg and i2v_cfg.enabled:
        from imaging import init_i2v_client
        init_i2v_client(
            api_key=i2v_cfg.resolve_api_key(),
            model=i2v_cfg.model,
            default_resolution=i2v_cfg.default_resolution,
            default_duration=i2v_cfg.default_duration,
        )

    # 初始化记忆管理器
    if _config and _config.memory.enabled:
        store = get_store()
        if store:
            global _memory_manager
            _memory_manager = MemoryManager(
                store=store,
                max_turns=_config.memory.max_turns_before_compress,
                summary_max_chars=_config.memory.summary_max_chars,
                recent_messages_to_keep=_config.memory.recent_messages_to_keep,
                llm_provider=None,  # 摘要使用 coordinator 的 LLM，在 compress 时单独创建
            )
            logger.info(f"   - 记忆管理器: 启用, max_turns={_config.memory.max_turns_before_compress}")

    # 初始化跨会话记忆召回
    store = get_store()
    if store:
        global _memory_recall
        _memory_recall = MemoryRecall(store)
        global _preference_learner
        _preference_learner = PreferenceLearner(store)
        logger.info("   - 跨会话记忆召回: 启用")
        logger.info("   - 用户偏好学习: 启用")

    logger.info("✅ MemoX 启动完成")
    logger.info(f"   - RAG 引擎: {len(_rag_engine.list_documents())} 个文档")


# ==================== 认证 API ====================

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
@limiter.limit("10/minute")
async def login(request: Request, login_req: LoginRequest) -> dict:
    """用户登录，返回 Bearer Token"""
    auth = get_auth_manager()
    token = auth.login(login_req.username, login_req.password)
    if not token:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user_info = auth.get_user_info(token)
    return {
        "token": token,
        "token_type": "Bearer",
        "user": user_info,
    }


@app.post("/api/auth/logout")
async def logout(request: Request) -> dict:
    """用户登出，吊销 Token（需携带有效 Token）"""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    auth = _get_auth_from_request(request)
    if not token or not auth.validate_token(token):
        raise HTTPException(status_code=401, detail="未登录或 Token 已过期")
    auth.logout(token)
    return {"success": True}


@app.get("/api/auth/me")
async def me(request: Request) -> dict:
    """获取当前登录用户信息"""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user_info = _get_auth_from_request(request).get_user_info(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="未登录")
    return user_info


# ==================== 知识库 API ====================

@app.get("/api/documents")
async def list_documents() -> list[DocumentResponse]:
    """列出所有文档"""
    docs = _rag_engine.list_documents()
    return [
        DocumentResponse(
            id=d.id,
            filename=d.filename,
            type=d.type,
            chunk_count=d.chunk_count,
            created_at=d.created_at,
            size=d.size,
            group_id=d.group_id,
        )
        for d in docs
    ]


@app.post("/api/documents")
@limiter.limit("5/minute")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    group_id: str = Form(default="ungrouped"),
) -> DocumentResponse:
    """上传文档"""
    import asyncio
    import traceback

    upload_dir = Path(_config.knowledge_base.upload_directory)
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 保存文件
    file_path = upload_dir / f"{uuid.uuid4().hex}_{file.filename}"
    try:
        content = await file.read()
        file_path.write_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件保存失败: {str(e)}") from e

    logger.info(f"[UPLOAD] File saved: {file_path}, size: {len(content)} bytes")

    # 添加到知识库（带整体超时）
    try:
        # 总超时 300 秒（5分钟），给大文件处理足够时间
        result = await asyncio.wait_for(
            _rag_engine.add_document(file_path, group_id=group_id, original_filename=file.filename),
            timeout=300.0
        )
        doc_info = result.doc_info
        action = result.action
        logger.info(f"[UPLOAD] Document added: {doc_info.id} (action={action})")
        return DocumentResponse(
            id=doc_info.id,
            filename=doc_info.filename,
            type=doc_info.type,
            chunk_count=doc_info.chunk_count,
            created_at=doc_info.created_at,
            size=doc_info.size,
            group_id=doc_info.group_id,
            action=action,
        )
    except asyncio.TimeoutError as e:
        logger.error("[UPLOAD ERROR] 文档处理超时")
        # 清理文件
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail="文档处理超时，请尝试上传更小的文件或简化文档格式") from e
    except TimeoutError as e:
        logger.error(f"[UPLOAD ERROR] {e}")
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail=str(e)) from e
    except ValueError as e:
        logger.error(f"[UPLOAD ERROR] {e}")
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=str(e)) from e
    except Exception as e:
        logger.exception(f"[UPLOAD ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        # 清理文件
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"文档处理失败: {type(e).__name__}: {str(e)}") from e


@app.post("/api/documents/url")
async def import_url(request: URLRequest) -> DocumentResponse:
    """从 URL 抓取网页并导入知识库"""
    import re
    url = request.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="URL 必须以 http:// 或 https:// 开头")

    doc_id = uuid.uuid4().hex[:8]
    parser = WebPageParser()

    try:
        doc_info_raw = await asyncio.wait_for(
            parser.fetch_url(url, doc_id),
            timeout=35.0,
        )
    except asyncio.TimeoutError as e:
        raise HTTPException(status_code=504, detail="网页抓取超时") from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"网页抓取失败: {type(e).__name__}: {str(e)}") from e

    # 分块并存入向量库
    chunks = await parser.chunk(doc_info_raw, chunk_size=500, overlap=50)
    for chunk in chunks:
        chunk.metadata["doc_id"] = doc_id
        chunk.metadata["filename"] = url
        chunk.metadata["type"] = "webpage"
        chunk.metadata["group_id"] = "ungrouped"

    if chunks:
        BATCH_SIZE = 100
        for i in range(0, len(chunks), BATCH_SIZE):
            await _rag_engine.vector_store.add_chunks(chunks[i:i + BATCH_SIZE])

    doc_info = DocumentInfo(
        id=doc_id,
        filename=url,
        type="webpage",
        chunk_count=len(chunks),
        created_at=__import__("datetime").datetime.now().isoformat(),
        size=len(doc_info_raw.content.encode()),
    )
    _rag_engine._documents[doc_id] = doc_info

    return DocumentResponse(
        id=doc_info.id,
        filename=doc_info.filename,
        type=doc_info.type,
        chunk_count=doc_info.chunk_count,
        created_at=doc_info.created_at,
        size=doc_info.size,
    )


@app.delete("/api/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """删除文档（仅管理员）"""
    success = await _rag_engine.delete_document(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    _audit_log(request, user, "delete", "document", doc_id)
    return {"success": True, "message": "Document deleted"}


# ==================== 分组 API ====================

@app.get("/api/groups")
async def list_groups() -> list[dict]:
    """列出所有分组（含各组文档数）"""
    docs = _rag_engine.list_documents()
    count_map: dict[str, int] = {}
    for doc in docs:
        count_map[doc.group_id] = count_map.get(doc.group_id, 0) + 1
    return [
        {
            "id": g.id,
            "name": g.name,
            "color": g.color,
            "created_at": g.created_at,
            "doc_count": count_map.get(g.id, 0),
        }
        for g in _group_store.list_groups()
    ]


@app.post("/api/groups")
async def create_group(request: GroupCreate) -> dict:
    """新建分组"""
    group = _group_store.create_group(request.name, request.color)
    return {"id": group.id, "name": group.name, "color": group.color, "created_at": group.created_at, "doc_count": 0}


@app.put("/api/groups/{group_id}")
async def update_group(
    group_id: str,
    request: GroupUpdate,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """修改分组名称或颜色（仅管理员）"""
    try:
        group = _group_store.update_group(group_id, request.name, request.color)
        return {"id": group.id, "name": group.name, "color": group.color, "created_at": group.created_at}
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Group not found") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/groups/{group_id}")
async def delete_group(
    group_id: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """删除分组，其下文档自动归回未分组（仅管理员）"""
    try:
        docs = _rag_engine.list_documents()
        for doc in docs:
            if doc.group_id == group_id:
                _rag_engine.move_document_group(doc.id, UNGROUPED_ID)
        _group_store.delete_group(group_id)
        _audit_log(request, user, "delete", "group", group_id)
        return {"success": True}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.put("/api/documents/{doc_id}/group")
async def move_document_group(
    doc_id: str,
    request: MoveDocumentGroup,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """修改文档所属分组（仅管理员）"""
    if not _group_store.get_group(request.group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    success = _rag_engine.move_document_group(doc_id, request.group_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"success": True}


@app.get("/api/documents/{doc_id}/chunks")
async def get_document_chunks(doc_id: str) -> dict:
    """获取文档的所有分块内容"""
    chunks = _rag_engine.get_document_chunks(doc_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="Document not found or has no chunks")
    filename = chunks[0].get("metadata", {}).get("filename", "unknown")
    return {
        "doc_id": doc_id,
        "filename": filename,
        "chunk_count": len(chunks),
        "chunks": [
            {
                "id": c["id"],
                "content": c["content"],
                "index": c["chunk_index"],
            }
            for c in chunks
        ],
    }


@app.get("/api/documents/search")
async def search_documents(q: str, group_ids: str | None = None) -> dict:
    """全文搜索文档"""
    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    gids = group_ids.split(",") if group_ids else None
    results = await _rag_engine.search(q, group_ids=gids, top_k=20)
    # 构建有效文档映射（id -> 显示名称）
    valid_docs = {d.id: d.filename for d in _rag_engine.list_documents()}
    # 按文档去重：每个文档只保留得分最高的一条结果
    seen_docs: dict[str, dict] = {}
    for r in results:
        doc_id = r.metadata.get("doc_id", "")
        if doc_id not in valid_docs:
            continue
        if doc_id not in seen_docs or r.score > seen_docs[doc_id]["score"]:
            seen_docs[doc_id] = {
                "doc_id": doc_id,
                "filename": valid_docs[doc_id],
                "content": r.content,
                "score": r.score,
                "chunk_index": r.metadata.get("chunk_index", 0),
                "group_id": r.metadata.get("group_id", "ungrouped"),
            }
    return {
        "query": q,
        "results": sorted(seen_docs.values(), key=lambda x: x["score"], reverse=True),
    }


# ==================== RAG 问答 API ====================


_HISTORY_TURN_LIMIT = 20  # 最多注入的历史消息数（10 轮对话）


def _load_chat_history(session_id: str) -> list[dict]:
    """从持久化层加载历史消息（不含当前轮次），转换为 LLM messages 格式。

    - 仅取最近 _HISTORY_TURN_LIMIT 条
    - 去除图像/视频 markdown 与 [[IMAGE:...]] / [[VIDEO:...]] / [[I2V:...]] 标记，降低 token 消耗
    """
    store = get_store()
    if not store:
        return []
    try:
        rows = store.get_session_messages(session_id)
    except Exception:
        return []
    if not rows:
        return []
    rows = rows[-_HISTORY_TURN_LIMIT:]
    cleaned: list[dict] = []
    for r in rows:
        role = r.get("role")
        if role not in ("user", "assistant"):
            continue
        content = r.get("content") or ""
        content = _re.sub(r"\[\[(IMAGE|VIDEO|I2V):\s*.+?\]\]", "", content, flags=_re.DOTALL)
        content = _re.sub(r"!\[[^\]]*\]\(https?://[^\s)]+\)", "", content)
        content = _re.sub(r"\[video:[^\]]*\]\(https?://[^\s)]+\)", "", content)
        content = _re.sub(r"\n{3,}", "\n\n", content).strip()
        if not content:
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned


def _inject_history(messages: list[dict], session_id: str) -> list[dict]:
    """在最后一条 user 消息前插入历史对话（支持摘要压缩）。

    如果 MemoryManager 已对该会话做过压缩，则注入摘要 + 未归档消息；
    否则仅注入最近 _HISTORY_TURN_LIMIT 条原始消息。
    同时注入用户已学习的偏好。
    """
    # 优先使用 MemoryManager 获取压缩后上下文
    if _memory_manager:
        summary, history = _memory_manager.get_context(session_id)
        if history or summary:
            # 将消息按 role 分组注入
            if summary:
                # 在系统消息（如有）后插入摘要提示
                summary_msg = {
                    "role": "system",
                    "content": f"【会话记忆摘要】{summary}\n（以上为历史对话摘要，以下为最近对话）",
                }
                # 找到 system 消息之后、user 消息之前的位置
                inject_idx = 1  # 默认插在 system 之后
                for i, m in enumerate(messages[:-1]):
                    if m.get("role") == "system":
                        inject_idx = i + 1
                messages = messages[:inject_idx] + [summary_msg] + messages[inject_idx:]
            # 在最后一条 user 消息前注入历史
            if not messages or messages[-1].get("role") != "user":
                messages = messages + history
            else:
                messages = messages[:-1] + history + messages[-1:]
            # 注入用户偏好（在最后）
            if _preference_learner:
                pref_text = _preference_learner.get_and_format(limit=8)
                if pref_text:
                    messages = messages + [{"role": "system", "content": pref_text}]
            return messages

    # 回退：原始行为
    history = _load_chat_history(session_id)
    if not history:
        return messages
    if not messages or messages[-1].get("role") != "user":
        messages = messages + history
    else:
        messages = messages[:-1] + history + messages[-1:]
    # 偏好注入（fallback 路径也注入）
    if _preference_learner:
        pref_text = _preference_learner.get_and_format(limit=8)
        if pref_text:
            messages = messages + [{"role": "system", "content": pref_text}]
    return messages


def _resolve_chat_llm(worker_id: str | None) -> tuple:
    """根据 worker_id 解析 LLM 配置，返回 (provider, model, temperature, max_tokens, resolved_worker_id)。
    不传 worker_id 时使用 coordinator 配置。"""
    if worker_id:
        worker_pool = get_worker_pool()
        if worker_pool and worker_id in worker_pool._workers:
            w = worker_pool._workers[worker_id]
            pcfg = _config.providers.get(w.config.provider_type)
            if not pcfg:
                raise HTTPException(status_code=400, detail=f"Worker '{worker_id}' 的 provider '{w.config.provider_type}' 未配置")
            provider = create_provider(
                w.config.provider_type,
                pcfg.resolve_api_key(),
                base_url=pcfg.base_url,
                headers=pcfg.headers,
            )
            return provider, w.config.model, w.config.temperature, w.config.max_tokens, worker_id
        else:
            raise HTTPException(status_code=404, detail=f"Worker '{worker_id}' 不存在")

    # 默认使用 coordinator
    pcfg = _config.providers.get(_config.coordinator.provider)
    if not pcfg:
        raise HTTPException(status_code=500, detail="LLM provider not configured")
    api_key = pcfg.resolve_api_key()
    if not api_key:
        raise HTTPException(status_code=500, detail=f"LLM API Key 未配置（provider: {_config.coordinator.provider}）")
    provider = create_provider(
        _config.coordinator.provider,
        api_key,
        base_url=pcfg.base_url,
        headers=pcfg.headers,
    )
    return provider, _config.coordinator.model, _config.coordinator.temperature, _config.coordinator.max_tokens, None


@app.post("/api/chat")
@limiter.limit("20/minute")
async def chat(request: Request, chat_req: ChatRequest) -> dict:
    """聊天问答（非流式）"""
    session_id = chat_req.session_id or str(uuid.uuid4())[:8]

    # 获取或创建会话
    session = _rag_engine.get_session(session_id)
    if not session:
        session = _rag_engine.create_session()

    # 添加用户消息
    _rag_engine.add_message(session_id, "user", chat_req.message)

    # RAG 检索
    search_results: list[SearchResult] = []
    if chat_req.use_rag:
        search_results = await _rag_engine.search(chat_req.message, group_ids=chat_req.active_group_ids)

    # 构建提示
    from imaging import get_image_client, get_video_client
    image_client = get_image_client()
    video_client = get_video_client()
    media_instruction = ""
    if image_client:
        media_instruction += (
            "\n\n【图像生成能力】当用户明确要求生成/绘制/画一张图片、插图、海报、参考图、示意图等视觉内容时，"
            "你必须在回答的合适位置输出形如 [[IMAGE: 英文或中文的详细画面描述]] 的标记（每张图片一个标记）。"
            "系统会自动将标记替换为真实图像展示给用户。描述要具体、包含风格、主体、场景、光线等要素。"
            "不要在标记外再贴链接，不要解释这是占位符。若用户未要求图片，则不要输出此标记。"
        )
    if video_client:
        media_instruction += (
            "\n\n【视频生成能力】当用户明确要求生成/制作一段视频、短片、动画、演示视频等动态视觉内容时，"
            "你必须在回答的合适位置输出形如 [[VIDEO: 详细画面与动作描述]] 的标记（每段视频一个标记）。"
            "描述要包含主体、动作、场景、时长氛围等。视频生成耗时较长（30s~数分钟），请如实告知用户需要等待。"
            "不要在标记外贴链接，不要解释这是占位符。若用户未要求视频，则不要输出此标记。"
        )
    if search_results:
        messages = _rag_engine.build_rag_prompt(chat_req.message, search_results)
        if media_instruction:
            messages[0]["content"] = (messages[0]["content"] or "") + media_instruction
    else:
        messages = [
            {"role": "system", "content": "你是一个智能助手。" + media_instruction},
            {"role": "user", "content": chat_req.message},
        ]

    # 注入历史对话（从持久化层读取，刷新/重启后仍可延续上下文）
    messages = _inject_history(messages, session_id)

    # 调用 LLM（根据 worker_id 选择模型配置）
    provider, model, temperature, max_tokens, resolved_worker_id = _resolve_chat_llm(chat_req.worker_id)

    try:
        response = await provider.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:
        import httpx as _httpx
        if isinstance(e, _httpx.HTTPStatusError):
            status = e.response.status_code
            if status == 401:
                raise HTTPException(status_code=502, detail="LLM API Key 无效或已过期") from e
            elif status == 429:
                raise HTTPException(status_code=429, detail="LLM API 请求频率超限，请稍后重试") from e
            else:
                raise HTTPException(status_code=502, detail=f"LLM 服务返回错误 {status}") from e
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {type(e).__name__}: {str(e)}") from e

    raw_answer = response.content or "抱歉，我无法回答这个问题。"

    # 解析 [[IMAGE: ...]] / [[VIDEO: ...]] 标记并并行生成媒体
    import re as _re
    image_prompts: list[str] = _re.findall(r"\[\[IMAGE:\s*(.+?)\]\]", raw_answer, flags=_re.DOTALL)
    video_prompts: list[str] = _re.findall(r"\[\[VIDEO:\s*(.+?)\]\]", raw_answer, flags=_re.DOTALL)
    i2v_pairs = parse_i2v_markers(raw_answer)
    display_text = _re.sub(r"\[\[(IMAGE|VIDEO|I2V):\s*.+?\]\]", "", raw_answer, flags=_re.DOTALL).strip()

    image_results: list[dict] = []
    if image_prompts and image_client:
        for p in image_prompts:
            ptext = p.strip()
            try:
                urls = await image_client.generate(ptext)
                for u in urls:
                    image_results.append({"url": u, "prompt": ptext})
            except Exception as ie:
                image_results.append({"error": str(ie), "prompt": ptext})

    video_results: list[dict] = []
    if video_prompts and video_client:
        for p in video_prompts:
            ptext = p.strip()
            try:
                url = await video_client.generate(ptext)
                video_results.append({"url": url, "prompt": ptext})
            except Exception as ve:
                video_results.append({"error": str(ve), "prompt": ptext})

    i2v_results: list[dict] = []
    if i2v_pairs:
        from imaging import get_i2v_client
        i2v_client = get_i2v_client()
        if i2v_client:
            for image_url, prompt_text in i2v_pairs:
                try:
                    url = await i2v_client.generate(image_url=image_url, prompt=prompt_text)
                    i2v_results.append({"url": url, "prompt": prompt_text, "source_image_url": image_url})
                except Exception as ve:
                    i2v_results.append({"error": str(ve), "prompt": prompt_text, "image_url": image_url})

    answer = display_text
    md_parts: list[str] = []
    for r in image_results:
        if r.get("url"):
            md_parts.append(f"![{r.get('prompt','')}]({r['url']})")
        elif r.get("error"):
            md_parts.append(f"_图像生成失败: {r['error']}_")
    for r in video_results:
        if r.get("url"):
            md_parts.append(f"[video:{r.get('prompt','')}]({r['url']})")
        elif r.get("error"):
            md_parts.append(f"_视频生成失败: {r['error']}_")
    for r in i2v_results:
        if r.get("url"):
            md_parts.append(f"[video:{r.get('prompt','')}]({r['url']})")
    if md_parts:
        answer = (display_text + "\n\n" + "\n\n".join(md_parts)).strip()

    # 添加助手消息
    _rag_engine.add_message(session_id, "assistant", answer)

    # 持久化消息
    store = get_store()
    if store:
        store.save_message(session_id, "user", chat_req.message)
        store.save_message(session_id, "assistant", answer)
        # 自动生成会话标题（取用户第一条消息前 30 字）
        existing = store.get_session_messages(session_id)
        if len(existing) <= 2:  # 第一轮对话
            title = chat_req.message[:30].strip()
            store.update_session_title(session_id, title)
        # 记忆压缩检查（超过 max_turns 后触发摘要）
        if _memory_manager and _orchestrator:
            _memory_manager.compress_if_needed(session_id, _orchestrator._provider)

    # 构建结构化引用（P4-3）
    # 从 LLM 回答中解析出引用的 [ref-N] 编号
    cited_ref_ids = _rag_engine.extract_citations_from_text(answer)
    citations: list[dict] = []
    for ref_id in cited_ref_ids:
        # ref_id 格式: "ref-N" → N 为 1-based 索引
        try:
            idx = int(ref_id.split("-")[1]) - 1
            if 0 <= idx < len(search_results):
                r = search_results[idx]
                citation = r.citation
                if citation:
                    citations.append(citation.to_dict())
        except (ValueError, IndexError):
            pass

    return {
        "session_id": session_id,
        "answer": answer,
        "worker_id": resolved_worker_id,
        "images": image_results,
        "videos": video_results,
        "i2v": i2v_results,
        "citations": citations,  # P4-3: 结构化引用来源（前端可渲染可点击链接）
        "sources": [
            {
                "content": r.content[:200] + "..." if len(r.content) > 200 else r.content,
                "score": r.score,
                "filename": r.metadata.get("filename", "unknown"),
                "doc_id": r.metadata.get("doc_id", ""),
                "chunk_index": r.metadata.get("chunk_index", 0),
            }
            for r in search_results
        ],
    }


@app.get("/api/chat/sessions")
async def list_chat_sessions(archived: str | None = None) -> list[dict]:
    """列出聊天会话历史

    archived 查询参数:
        - 省略 / "0" / "false": 仅未归档（默认）
        - "1" / "true":          仅已归档
        - "all":                 全部
    """
    store = get_store()
    if not store:
        return []

    if archived is None:
        flag: bool | None = False
    else:
        v = archived.lower()
        if v == "all":
            flag = None
        elif v in ("1", "true", "yes"):
            flag = True
        else:
            flag = False
    return store.list_sessions(archived=flag)


class SessionUpdateRequest(BaseModel):
    title: str | None = None
    archived: bool | None = None


@app.patch("/api/chat/sessions/{session_id}")
async def update_chat_session(session_id: str, request: SessionUpdateRequest) -> dict:
    """更新会话属性：重命名 / 归档 / 取消归档"""
    store = get_store()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    if request.title is None and request.archived is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    touched = False
    if request.title is not None:
        title = request.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        if len(title) > 100:
            raise HTTPException(status_code=400, detail="Title too long (max 100)")
        if not store.rename_session(session_id, title):
            raise HTTPException(status_code=404, detail="Session not found")
        touched = True

    if request.archived is not None:
        if not store.set_session_archived(session_id, request.archived):
            raise HTTPException(status_code=404, detail="Session not found")
        touched = True

    return {"success": touched}


@app.get("/api/chat/sessions/{session_id}/messages")
async def get_session_messages(session_id: str) -> list[dict]:
    """获取会话消息历史"""
    store = get_store()
    if store:
        messages = store.get_session_messages(session_id)
        if messages:
            return messages
    raise HTTPException(status_code=404, detail="Session not found")


# ==================== 记忆管理 API ====================


class MemorySummaryRequest(BaseModel):
    session_id: str
    summary: str
    force: bool = False  # 强制重新压缩


class MemoryConfigRequest(BaseModel):
    enabled: bool | None = None
    max_turns_before_compress: int | None = None
    summary_max_chars: int | None = None


@app.post("/api/chat/sessions/{session_id}/compress")
async def compress_session(session_id: str, force: bool = False) -> dict:
    """手动触发会话记忆压缩（摘要）"""
    if not _memory_manager:
        raise HTTPException(status_code=503, detail="记忆管理器未启用")
    provider = _orchestrator._provider if _orchestrator else None
    if not provider:
        raise HTTPException(status_code=503, detail="LLM provider 未就绪")
    try:
        new_summary, archived_count = _memory_manager.compress_if_needed(session_id, provider, force=force)
        return {
            "success": True,
            "session_id": session_id,
            "summary": new_summary,
            "archived_messages": archived_count,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"压缩失败: {e}") from e


@app.get("/api/chat/sessions/{session_id}/memory")
async def get_session_memory(session_id: str) -> dict:
    """获取会话记忆状态（摘要 + 未归档消息数）"""
    if not _memory_manager:
        raise HTTPException(status_code=503, detail="记忆管理器未启用")
    summary, history = _memory_manager.get_context(session_id)
    return {
        "session_id": session_id,
        "summary": summary,
        "uncompressed_count": len(history),
        "is_compressed": summary is not None,
    }


@app.post("/api/chat/sessions/{session_id}/memory")
async def update_session_memory_summary(session_id: str, req: MemorySummaryRequest) -> dict:
    """手动更新会话摘要（用户编辑记忆）"""
    if not _memory_manager:
        raise HTTPException(status_code=503, detail="记忆管理器未启用")
    store = get_store()
    if not store:
        raise HTTPException(status_code=500, detail="存储未初始化")
    store.save_session_summary(session_id, req.summary)
    return {"success": True, "session_id": session_id}


@app.get("/api/memory/config")
async def get_memory_config() -> dict:
    """获取记忆管理器全局配置"""
    if not _config:
        raise HTTPException(status_code=500, detail="配置未初始化")
    mc = _config.memory
    return {
        "enabled": mc.enabled,
        "max_turns_before_compress": mc.max_turns_before_compress,
        "summary_max_chars": mc.summary_max_chars,
        "recent_messages_to_keep": mc.recent_messages_to_keep,
    }


@app.patch("/api/memory/config")
async def update_memory_config(req: MemoryConfigRequest) -> dict:
    """更新记忆管理器运行时配置（仅影响当前进程，重启后恢复）"""
    if not _memory_manager or not _config:
        raise HTTPException(status_code=503, detail="记忆管理器未启用")
    if req.enabled is not None:
        _config.memory.enabled = req.enabled
    if req.max_turns_before_compress is not None:
        _config.memory.max_turns_before_compress = req.max_turns_before_compress
        _memory_manager._max_turns = req.max_turns_before_compress
    if req.summary_max_chars is not None:
        _config.memory.summary_max_chars = req.summary_max_chars
        _memory_manager._summary_max_chars = req.summary_max_chars
    return {"success": True}


@app.delete("/api/chat/sessions/{session_id}/memory")
async def clear_session_memory(
    session_id: str,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    """清除会话记忆（删除摘要，重置为未压缩状态，仅登录用户）"""
    if not _memory_manager:
        raise HTTPException(status_code=503, detail="记忆管理器未启用")
    store = get_store()
    if not store:
        raise HTTPException(status_code=500, detail="存储未初始化")
    store.save_session_summary(session_id, "")
    store.clear_archived_messages(session_id)
    return {"success": True, "session_id": session_id}


# ==================== 跨会话记忆 API ====================


class CreateMemoryRequest(BaseModel):
    content: str
    category: str = "general"
    importance: int = 3
    user_id: str | None = None
    session_id: str | None = None


class UpdateMemoryRequest(BaseModel):
    content: str | None = None
    category: str | None = None
    importance: int | None = None
    metadata: dict | None = None


@app.get("/api/memories")
async def list_memories(
    category: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
) -> dict:
    """列出所有记忆（支持按分类/用户过滤）"""
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    memories = _memory_recall.get_all(user_id=user_id, category=category, limit=limit)
    return {"memories": memories, "total": len(memories)}


@app.get("/api/memories/search")
async def search_memories(
    q: str,
    user_id: str | None = None,
    limit: int = 5,
) -> dict:
    """根据关键词检索相关记忆"""
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    if len(q) < 2:
        raise HTTPException(status_code=400, detail="查询关键词至少需要2个字符")
    memories = _memory_recall.recall(query=q, user_id=user_id, limit=limit)
    return {"memories": memories, "query": q, "count": len(memories)}


@app.post("/api/memories")
async def create_memory(req: CreateMemoryRequest) -> dict:
    """手动创建一条记忆"""
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="记忆内容不能为空")
    memory_id = _memory_recall.save_memory(
        content=req.content,
        user_id=req.user_id,
        category=req.category,
        importance=req.importance,
        session_id=req.session_id,
    )
    return {"success": True, "id": memory_id}


@app.get("/api/memories/{memory_id}")
async def get_memory(memory_id: str) -> dict:
    """获取单条记忆详情"""
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    memory = _memory_recall.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return memory


@app.patch("/api/memories/{memory_id}")
async def update_memory(memory_id: str, req: UpdateMemoryRequest) -> dict:
    """更新记忆内容/分类/重要性"""
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    success = _memory_recall.update_memory(memory_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"success": True}


@app.delete("/api/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    """删除一条记忆（仅登录用户）"""
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    if not _memory_recall.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"success": True, "id": memory_id}


@app.post("/api/chat/sessions/{session_id}/extract-memories")
async def extract_memories_from_session(session_id: str) -> dict:
    """从会话历史中自动提取记忆（手动触发）"""
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    store = get_store()
    if not store:
        raise HTTPException(status_code=500, detail="存储未初始化")
    messages = store.get_session_messages(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="会话不存在或无消息")
    provider = _orchestrator._provider if _orchestrator else None
    count = _memory_recall.save_from_conversation(
        messages=messages,
        session_id=session_id,
        llm_provider=provider,
    )
    return {"success": True, "session_id": session_id, "extracted_count": count}


@app.delete("/api/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    """删除聊天会话（仅登录用户）"""
    store = get_store()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    if not store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    # 同时清理 RAG 引擎内存中的会话
    if _rag_engine:
        _rag_engine.delete_session(session_id)
    return {"success": True}


class SummarizeTaskRequest(BaseModel):
    task_type: str | None = None  # 用户已选择的任务类型（如：撰写文档 / 撰写ppt / 开发应用 / 配置定时任务 / 生成参考图片视频）


_TASK_TYPE_OPTIONS = [
    "撰写文档",
    "撰写PPT",
    "开发应用",
    "配置定时任务",
    "生成参考图片视频",
]


@app.post("/api/chat/sessions/{session_id}/summarize-task")
async def summarize_session_as_task(session_id: str, request: SummarizeTaskRequest) -> dict:
    """把一段聊天会话提炼为可直接交给任务执行引擎的任务描述。

    - 若模型能明确用户想要的产物形态，返回 {status: "ready", summary: "..."}。
    - 若不确定，返回 {status: "need_clarification", question: "...", options: [...]}，
      由前端让用户选定任务类型后再次带 task_type 调用本接口。
    """
    store = get_store()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")

    history = store.get_session_messages(session_id)
    if not history:
        raise HTTPException(status_code=404, detail="Session not found or empty")

    # 拼接对话
    convo_lines = []
    for m in history:
        role = {"user": "用户", "assistant": "AI助手", "system": "系统"}.get(m["role"], m["role"])
        convo_lines.append(f"【{role}】{m['content']}")
    conversation = "\n\n".join(convo_lines)

    options_str = "、".join(_TASK_TYPE_OPTIONS)
    chosen_type_hint = (
        f"\n\n用户已明确任务类型为：「{request.task_type}」。请按该类型组织摘要，不要再询问。"
        if request.task_type else ""
    )

    system_prompt = f"""你是一个任务编排助手。用户刚刚与另一个 AI 助手进行了一段对话，现在希望把对话内容提炼为一个独立、可执行的任务描述，交给下游 worker 执行。

你的职责：
1. 阅读整段对话，识别用户真实意图与最终期望的"产物"（交付物）。
2. 如果你能明确判断交付物形态（例如 {options_str}），就直接输出一段自包含的任务描述。
3. 如果你无法唯一确定交付物形态，就向用户提一个澄清问题，列出候选任务类型供用户选择。

严格使用以下 JSON 格式输出，不要输出任何 JSON 之外的文字、注释或 markdown 代码块：

情况 A（可直接生成）：
{{"status": "ready", "summary": "<一段自包含的任务描述，包含：背景、目标、关键输入/约束、期望产物与交付形式>"}}

情况 B（需要用户澄清任务类型）：
{{"status": "need_clarification", "question": "<给用户看的简短追问>", "options": ["撰写文档", "撰写PPT", ...]}}

要求：
- summary 用中文书写，需要承载下游 worker 执行所需的全部关键信息，不要保留"你/我"这类对话口吻，改为第三人称任务表述。
- summary 结尾应显式标注"交付形式：xxx"。
- 候选 options 必须从以下列表中选取：{options_str}。{chosen_type_hint}
"""

    user_prompt = f"以下是完整的对话历史，请按要求输出 JSON：\n\n{conversation}"

    provider, model, temperature, max_tokens, _ = _resolve_chat_llm(None)

    try:
        response = await provider.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            temperature=0.3,
            max_tokens=max_tokens,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {type(e).__name__}: {str(e)}") from e

    raw = (response.content or "").strip()

    # 兼容模型误加的 ```json 代码块
    import re as _re
    fenced = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, _re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        brace = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if brace:
            raw = brace.group(0)

    try:
        parsed = json.loads(raw)
    except Exception:
        # 解析失败则把原始内容当作 summary 返回，避免前端彻底卡住
        return {"status": "ready", "summary": response.content or "", "raw_fallback": True}

    status = parsed.get("status")
    if status == "need_clarification" and not request.task_type:
        # 校准 options
        opts = [o for o in (parsed.get("options") or []) if o in _TASK_TYPE_OPTIONS]
        if not opts:
            opts = list(_TASK_TYPE_OPTIONS)
        return {
            "status": "need_clarification",
            "question": parsed.get("question") or "请选择期望的任务类型：",
            "options": opts,
        }

    summary = parsed.get("summary") or ""
    if request.task_type and request.task_type not in summary:
        summary = f"{summary}\n\n任务类型：{request.task_type}".strip()
    return {"status": "ready", "summary": summary}


@app.post("/api/chat/stream")
@limiter.limit("20/minute")
async def chat_stream(request: Request, chat_req: ChatRequest):
    """流式聊天问答"""
    # 提取值避免嵌套函数闭包歧义
    _session_id = chat_req.session_id or str(uuid.uuid4())[:8]
    _message = chat_req.message
    _use_rag = chat_req.use_rag
    _active_group_ids = chat_req.active_group_ids
    _worker_id = chat_req.worker_id

    async def generate():
        # 获取或创建会话
        session = _rag_engine.get_session(_session_id)
        if not session:
            session = _rag_engine.create_session()

        # 添加用户消息
        _rag_engine.add_message(_session_id, "user", _message)

        # RAG 检索
        search_results: list[SearchResult] = []
        if _use_rag:
            search_results = await _rag_engine.search(_message, group_ids=_active_group_ids)

            # 先发送检索结果（包含完整元数据，P4-3 引用支持）
            yield f"data: {json.dumps({'type': 'sources', 'data': [
                {'filename': r.metadata.get('filename', 'unknown'), 'score': r.score,
                 'doc_id': r.metadata.get('doc_id', ''), 'chunk_index': r.metadata.get('chunk_index', 0)}
                for r in search_results
            ]})}\n\n"

        # 构建提示
        from imaging import get_image_client, get_video_client
        image_client = get_image_client()
        video_client = get_video_client()
        media_instruction = ""
        if image_client:
            media_instruction += (
                "\n\n【图像生成能力】当用户明确要求生成/绘制/画一张图片、插图、海报、参考图、示意图等视觉内容时，"
                "你必须在回答的合适位置输出形如 [[IMAGE: 英文或中文的详细画面描述]] 的标记（每张图片一个标记）。"
                "系统会自动将标记替换为真实图像展示给用户。描述要具体、包含风格、主体、场景、光线等要素。"
                "不要在标记外再贴链接，不要解释这是占位符。若用户未要求图片，则不要输出此标记。"
            )
        if video_client:
            media_instruction += (
                "\n\n【视频生成能力】当用户明确要求生成/制作一段视频、短片、动画、演示视频等动态视觉内容时，"
                "你必须在回答的合适位置输出形如 [[VIDEO: 详细画面与动作描述]] 的标记（每段视频一个标记）。"
                "描述要包含主体、动作、场景、时长氛围等。视频生成耗时较长（30s~数分钟），请如实告知用户需要等待。"
                "不要在标记外贴链接，不要解释这是占位符。若用户未要求视频，则不要输出此标记。"
            )
        if search_results:
            messages = _rag_engine.build_rag_prompt(_message, search_results)
            if media_instruction:
                messages[0]["content"] = (messages[0]["content"] or "") + media_instruction
        else:
            messages = [
                {"role": "system", "content": "你是一个智能助手。" + media_instruction},
                {"role": "user", "content": _message},
            ]

        # 注入历史对话（从持久化层读取，刷新/重启后仍可延续上下文）
        messages = _inject_history(messages, _session_id)

        # 流式调用 LLM（根据 worker_id 选择模型配置）
        try:
            provider, model, temperature, max_tokens, resolved_worker_id = _resolve_chat_llm(_worker_id)
        except HTTPException as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': exc.detail})}\n\n"
            return

        try:
            response = await provider.chat_stream(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                on_chunk=lambda c: None,
            )

            # 解析 [[IMAGE: prompt]] / [[VIDEO: prompt]] / [[I2V: url | prompt]] 标记
            raw_text = response.content or ""
            image_prompts: list[str] = _re.findall(r"\[\[IMAGE:\s*(.+?)\]\]", raw_text, flags=_re.DOTALL)
            video_prompts: list[str] = _re.findall(r"\[\[VIDEO:\s*(.+?)\]\]", raw_text, flags=_re.DOTALL)
            display_text = _re.sub(r"\[\[(IMAGE|VIDEO|I2V):\s*.+?\]\]", "", raw_text, flags=_re.DOTALL).strip()
            i2v_pairs = parse_i2v_markers(raw_text)

            # 直接发送完整内容作为流
            for i in range(0, len(display_text), 10):
                chunk = display_text[i:i+10]
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
                await asyncio.sleep(0.02)

            # 生成图像并逐张推送
            image_urls: list[str] = []
            if image_prompts and image_client:
                for p in image_prompts:
                    prompt_text = p.strip()
                    yield f"data: {json.dumps({'type': 'image_pending', 'prompt': prompt_text})}\n\n"
                    try:
                        urls = await image_client.generate(prompt_text)
                        for u in urls:
                            image_urls.append(u)
                            yield f"data: {json.dumps({'type': 'image', 'url': u, 'prompt': prompt_text})}\n\n"
                    except Exception as ie:
                        yield f"data: {json.dumps({'type': 'image_error', 'prompt': prompt_text, 'message': str(ie)})}\n\n"

            # 生成视频并逐段推送
            video_urls: list[tuple[str, str]] = []  # (url, prompt)
            if video_prompts and video_client:
                for p in video_prompts:
                    prompt_text = p.strip()
                    yield f"data: {json.dumps({'type': 'video_pending', 'prompt': prompt_text})}\n\n"
                    try:
                        url = await video_client.generate(prompt_text)
                        video_urls.append((url, prompt_text))
                        yield f"data: {json.dumps({'type': 'video', 'url': url, 'prompt': prompt_text})}\n\n"
                    except Exception as ve:
                        yield f"data: {json.dumps({'type': 'video_error', 'prompt': prompt_text, 'message': str(ve)})}\n\n"

            # 图生视频（LLM 路径）
            i2v_results: list[tuple[str, str, str]] = []  # (url, prompt, source_image_url)
            if i2v_pairs:
                from imaging import get_i2v_client
                i2v_client = get_i2v_client()
                for image_url, prompt_text in i2v_pairs:
                    if not i2v_client:
                        yield f"data: {json.dumps({'type': 'i2v_error', 'prompt': prompt_text, 'image_url': image_url, 'message': '图生视频未启用'})}\n\n"
                        continue
                    yield f"data: {json.dumps({'type': 'i2v_pending', 'prompt': prompt_text, 'image_url': image_url})}\n\n"
                    try:
                        url = await i2v_client.generate(image_url=image_url, prompt=prompt_text)
                        i2v_results.append((url, prompt_text, image_url))
                        yield f"data: {json.dumps({'type': 'i2v', 'url': url, 'prompt': prompt_text, 'source_image_url': image_url})}\n\n"
                    except Exception as ie:
                        yield f"data: {json.dumps({'type': 'i2v_error', 'prompt': prompt_text, 'image_url': image_url, 'message': str(ie)})}\n\n"

            # 发送完成
            answer = display_text
            md_tail: list[str] = []
            if image_urls:
                md_tail += [f"![image]({u})" for u in image_urls]
            if video_urls:
                md_tail += [f"[video:{pt}]({u})" for u, pt in video_urls]
            if i2v_results:
                md_tail += [f"[video:{pt}]({u})" for u, pt, _ in i2v_results]
            if md_tail:
                answer = display_text + "\n\n" + "\n".join(md_tail)
            _rag_engine.add_message(_session_id, "assistant", answer)

            # 持久化消息
            store = get_store()
            if store:
                store.save_message(_session_id, "user", _message)
                store.save_message(_session_id, "assistant", answer)
                # 自动生成会话标题
                existing = store.get_session_messages(_session_id)
                if len(existing) <= 2:
                    title = _message[:30].strip()
                    store.update_session_title(_session_id, title)
                # 记忆压缩检查
                if _memory_manager and _orchestrator:
                    _memory_manager.compress_if_needed(_session_id, _orchestrator._provider)

            # P4-3: 从回答中解析引用的 [ref-N] 并构建结构化 citations
            cited_ref_ids = _rag_engine.extract_citations_from_text(answer)
            citations: list[dict] = []
            for ref_id in cited_ref_ids:
                try:
                    idx = int(ref_id.split("-")[1]) - 1
                    if 0 <= idx < len(search_results):
                        r = search_results[idx]
                        citation = r.citation
                        if citation:
                            citations.append(citation.to_dict())
                except (ValueError, IndexError):
                    pass

            yield f"data: {json.dumps({'type': 'done', 'session_id': _session_id, 'worker_id': resolved_worker_id, 'citations': citations})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ==================== 文生图 API ====================

class ImageGenRequest(BaseModel):
    prompt: str
    size: str | None = None
    n: int = 1


@app.post("/api/images/generate")
async def generate_image(request: ImageGenRequest) -> dict:
    """文生图（同步等待，返回图像 URL 列表）"""
    from imaging import get_image_client
    client = get_image_client()
    if not client:
        raise HTTPException(status_code=503, detail="图像生成未启用")
    try:
        urls = await client.generate(request.prompt, size=request.size, n=request.n)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图像生成失败: {e}") from e
    return {"urls": urls, "prompt": request.prompt}


class VideoGenRequest(BaseModel):
    prompt: str
    resolution: str | None = None
    ratio: str | None = None
    duration: int | None = None
    negative_prompt: str | None = None


@app.post("/api/videos/generate")
async def generate_video(request: VideoGenRequest) -> dict:
    """文生视频（异步任务，等待完成后返回视频 URL）"""
    from imaging import get_video_client
    client = get_video_client()
    if not client:
        raise HTTPException(status_code=503, detail="视频生成未启用")
    try:
        url = await client.generate(
            request.prompt,
            resolution=request.resolution,
            ratio=request.ratio,
            duration=request.duration,
            negative_prompt=request.negative_prompt,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"视频生成失败: {e}") from e
    return {"url": url, "prompt": request.prompt}


class I2VRequest(BaseModel):
    image_url: str
    prompt: str
    resolution: str | None = None
    duration: int | None = None
    negative_prompt: str | None = None


@app.post("/api/videos/i2v")
async def generate_i2v(request: I2VRequest) -> dict:
    """图生视频（异步任务，等待完成后返回视频 URL）"""
    from imaging import get_i2v_client
    client = get_i2v_client()
    if not client:
        raise HTTPException(status_code=503, detail="图生视频未启用")
    try:
        url = await client.generate(
            image_url=request.image_url,
            prompt=request.prompt,
            resolution=request.resolution,
            duration=request.duration,
            negative_prompt=request.negative_prompt,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"图生视频失败: {e}") from e
    return {"url": url, "prompt": request.prompt, "image_url": request.image_url}


@app.get("/api/files/{name:path}")
async def serve_upload(name: str, request: Request):
    """暴露 data/uploads/ 下的文件（供 DashScope 拉取图片等场景）"""
    # 从原始 URL 中提取文件名，防止编码绕过
    # 检查原始 URL 中是否含有编码斜杠或点点
    raw_name = request.url.path.removeprefix("/api/files/")
    if "/" in raw_name or "\\" in raw_name or ".." in raw_name:
        raise HTTPException(status_code=400, detail="非法文件名")
    # 同时对解码后的 name 参数做检查
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = (UPLOADS_DIR / name).resolve()
    try:
        path.relative_to(UPLOADS_DIR.resolve())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="非法路径") from e
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(str(path))


# ==================== 任务执行 API ====================

@app.post("/api/tasks")
async def create_task(request: TaskRequest) -> dict:
    """创建并执行任务（迭代编排器）"""
    if not _orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")

    timeout = request.timeout_seconds
    try:
        coro = _orchestrator.run(
            description=request.description,
            context=request.context or {},
            active_group_ids=request.active_group_ids,
        )
        if timeout:
            result = await asyncio.wait_for(coro, timeout=float(timeout))
        else:
            result = await coro
    except asyncio.TimeoutError as e:
        raise HTTPException(status_code=504, detail=f"任务执行超时（{timeout}秒）") from e

    suggestions = []
    if request.generate_suggestions and _task_planner:
        from agents.worker_pool import Task
        placeholder_task = Task(
            id=result.task_id,
            description=request.description,
        )
        suggestions = await _task_planner.generate_optimization_suggestions(
            placeholder_task,
            result.result_summary,
            request.context or {},
        )

    # 读取邮件通信日志
    mail_log = ""
    mail_log_path = Path(result.shared_dir) / "mail_log.txt"
    if mail_log_path.exists():
        mail_log = mail_log_path.read_text(encoding="utf-8")

    response_data = {
        "task_id": result.task_id,
        "result": result.result_summary,
        "shared_dir": result.shared_dir,
        "final_score": result.final_score,
        "iterations": [
            {
                "iteration": r.iteration,
                "score": r.score,
                "improvements": r.improvements,
            }
            for r in result.iterations
        ],
        "mail_log": mail_log,
        "suggestions": [
            {
                "type": s.type,
                "title": s.title,
                "description": s.description,
                "confidence": s.confidence,
                "code_snippet": s.code_snippet,
                "priority": s.priority,
            }
            for s in suggestions
        ],
    }

    # 缓存结果供后续查询
    _task_results[result.task_id] = response_data

    # 检测取消状态
    task_status = "cancelled" if result.result_summary == "(任务已取消)" else "completed"

    # 持久化任务
    store = get_store()
    if store:
        store.save_task({**response_data, "description": request.description, "status": task_status})

    return response_data


@app.get("/api/tasks")
async def list_tasks() -> list[dict]:
    """列出所有任务（合并内存 + SQLite 持久化）"""
    # 优先从 SQLite 加载持久化历史
    store = get_store()
    if store:
        persisted = store.list_tasks(limit=100)
        if persisted:
            return persisted

    # 回退到 TaskPlanner 的内存存储
    if _task_planner:
        return _task_planner.list_tasks()
    return []


@app.get("/api/tasks/running")
async def list_running_tasks() -> list[str]:
    """列出正在运行的任务 ID"""
    if not _orchestrator:
        return []
    return _orchestrator.list_running_tasks()


@app.get("/api/tasks/{task_id}/files")
async def get_task_files(task_id: str) -> dict:
    """获取任务 shared/ 目录的文件树和内容"""
    cached = _task_results.get(task_id)
    if not cached:
        store = get_store()
        if store:
            cached = store.get_task(task_id)
    if not cached or not cached.get("shared_dir"):
        raise HTTPException(status_code=404, detail="Task not found or no shared directory")

    shared_dir = Path(cached["shared_dir"])
    if not shared_dir.exists():
        return {"task_id": task_id, "files": []}

    files = []
    for file_path in sorted(shared_dir.rglob("*")):
        if not file_path.is_file():
            continue
        rel_path = str(file_path.relative_to(shared_dir))
        # 跳过 mail_log.txt（已在 mail_log 字段中返回）
        if file_path.name == "mail_log.txt":
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            content = "(二进制文件，无法预览)"
        files.append({
            "path": rel_path,
            "name": file_path.name,
            "size": file_path.stat().st_size,
            "content": content[:5000],  # 截断过长内容
            "truncated": len(content) > 5000 if isinstance(content, str) else False,
        })

    return {"task_id": task_id, "files": files}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    """获取任务详情（内存 → 缓存 → SQLite）"""
    # 1. TaskPlanner 内存中的任务（含子任务实时状态）
    if _task_planner:
        task = _task_planner.get_task(task_id)
        if task:
            return {
                "id": task.id,
                "description": task.description,
                "status": task.status.value,
                "result": task.result,
                "error": task.error,
                "sub_tasks": [
                    {
                        "id": st.id,
                        "description": st.description,
                        "status": st.status.value,
                        "result": st.result,
                        "error": st.error,
                        "assigned_agent": st.assigned_agent,
                    }
                    for st in task.sub_tasks
                ],
                "created_at": task.created_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
            }

    # 2. 内存缓存
    if task_id in _task_results:
        return _task_results[task_id]

    # 3. SQLite 持久化
    store = get_store()
    if store:
        persisted = store.get_task(task_id)
        if persisted:
            return persisted

    raise HTTPException(status_code=404, detail="Task not found")


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    """取消正在运行的任务"""
    if not _orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")
    if _orchestrator.cancel_task(task_id):
        return {"success": True, "message": f"Task {task_id} cancel requested"}
    raise HTTPException(status_code=404, detail="Task not found or not running")


class FeedbackRequest(BaseModel):
    feedback: str


@app.post("/api/tasks/{task_id}/feedback")
async def submit_task_feedback(task_id: str, request: FeedbackRequest) -> dict:
    """提交任务反馈（Human-in-the-Loop）"""
    if not _orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")
    if not _orchestrator.is_waiting_feedback(task_id):
        raise HTTPException(status_code=404, detail="Task is not waiting for feedback")
    _orchestrator.submit_feedback(task_id, request.feedback)
    return {"success": True}



# ==================== 定时任务 API ====================

class ScheduledTaskCreate(BaseModel):
    description: str
    cron: str
    enabled: bool = True
    active_group_ids: list[str] | None = None
    source_session_id: str | None = None


class ScheduledTaskUpdate(BaseModel):
    description: str | None = None
    cron: str | None = None
    enabled: bool | None = None
    active_group_ids: list[str] | None = None


def _serialize_scheduled(t: dict) -> dict:
    import json as _json
    try:
        gids = _json.loads(t.get("active_group_ids") or "[]")
    except Exception:
        gids = []
    return {**t, "active_group_ids": gids}


@app.get("/api/scheduled-tasks")
async def list_scheduled_tasks() -> list[dict]:
    store = get_store()
    if not store:
        return []
    return [_serialize_scheduled(t) for t in store.list_scheduled_tasks()]


@app.post("/api/scheduled-tasks")
async def create_scheduled_task_api(
    request: ScheduledTaskCreate,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    from datetime import datetime as _dt

    from scheduler import next_run_after, validate_cron

    store = get_store()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    ok, msg = validate_cron(request.cron)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Cron 表达式无效: {msg}")
    description = (request.description or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="任务描述不能为空")

    tid = str(uuid.uuid4())[:12]
    nxt = next_run_after(request.cron, _dt.now())
    next_iso = nxt.isoformat(timespec="minutes") if nxt else ""
    store.create_scheduled_task(
        task_id=tid,
        description=description,
        cron=request.cron,
        active_group_ids=request.active_group_ids or [],
        source_session_id=request.source_session_id or "",
        next_run_at=next_iso,
        enabled=request.enabled,
    )
    return _serialize_scheduled(store.get_scheduled_task(tid))


@app.patch("/api/scheduled-tasks/{task_id}")
async def update_scheduled_task_api(
    task_id: str,
    request: ScheduledTaskUpdate,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    from datetime import datetime as _dt

    from scheduler import next_run_after, validate_cron

    store = get_store()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    existing = store.get_scheduled_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    if request.cron is not None:
        ok, msg = validate_cron(request.cron)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Cron 表达式无效: {msg}")
    if request.description is not None and not request.description.strip():
        raise HTTPException(status_code=400, detail="任务描述不能为空")

    desc = request.description.strip() if request.description is not None else None
    next_iso: str | None = None
    if request.cron is not None or request.enabled is True:
        use_cron = request.cron if request.cron is not None else existing["cron"]
        nxt = next_run_after(use_cron, _dt.now())
        next_iso = nxt.isoformat(timespec="minutes") if nxt else ""

    store.update_scheduled_task(
        task_id,
        description=desc,
        cron=request.cron,
        enabled=request.enabled,
        active_group_ids=request.active_group_ids,
        next_run_at=next_iso,
    )
    return _serialize_scheduled(store.get_scheduled_task(task_id))


@app.delete("/api/scheduled-tasks/{task_id}")
async def delete_scheduled_task_api(
    task_id: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    store = get_store()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    if not store.delete_scheduled_task(task_id):
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    _audit_log(request, user, "delete", "scheduled_task", task_id)
    return {"success": True}


# ==================== Workflow API ====================

from workflow import (
    WorkflowEngine,
    WorkflowPersistence,
    parse_workflow_yaml,
    parse_workflow_yaml_file,
)
from workflow.engine import WorkflowRunStatus

_workflow_engine: WorkflowEngine | None = None
_workflow_persistence: WorkflowPersistence | None = None


def get_workflow_engine() -> WorkflowEngine:
    global _workflow_engine, _workflow_persistence
    if _workflow_engine is None:
        worker_pool = get_worker_pool()
        provider = create_provider()
        _workflow_persistence = WorkflowPersistence()
        _workflow_engine = WorkflowEngine(worker_pool, provider, _workflow_persistence)
    return _workflow_engine


@app.post("/api/workflows/validate")
async def validate_workflow(yaml_content: str) -> dict:
    """验证工作流 YAML 语法和 DAG 合法性"""
    try:
        wf = parse_workflow_yaml(yaml_content)
        errors = wf.validate()
        return {"valid": len(errors) == 0, "errors": errors, "step_count": len(wf.steps)}
    except Exception as e:
        return {"valid": False, "errors": [str(e)], "step_count": 0}


@app.post("/api/workflows/run")
async def run_workflow(
    yaml_content: str,
    context: dict | None = None,
) -> dict:
    """执行工作流（YAML 内容）"""
    try:
        wf = parse_workflow_yaml(yaml_content)
        engine = get_workflow_engine()
        run = await engine.execute(wf, context=context or {})
        return {
            "run_id": run.id,
            "status": run.status.value,
            "context_keys": list(run.context.keys()),
            "step_count": len(run.step_records),
        }
    except Exception as e:
        logger.error(f"[Workflow] 执行失败: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/workflows/runs")
async def list_workflow_runs(workflow_name: str | None = None, limit: int = 50) -> list[dict]:
    """列出工作流运行记录"""
    persistence = get_workflow_engine()  # ensure init
    runs = _workflow_persistence.list_runs(workflow_name=workflow_name, limit=limit)
    return [
        {
            "id": r.id,
            "workflow_name": r.workflow_name,
            "status": r.status.value,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
            "step_count": len(r.step_records),
        }
        for r in runs
    ]


@app.get("/api/workflows/runs/{run_id}")
async def get_workflow_run(run_id: str) -> dict:
    """获取工作流运行详情"""
    run = _workflow_persistence.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {
        "id": run.id,
        "workflow_name": run.workflow_name,
        "status": run.status.value,
        "context": run.context,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "paused_at": run.paused_at,
        "completed_at": run.completed_at,
        "steps": [
            {
                "step_id": r.step_id,
                "status": r.status.value,
                "output": r.output,
                "error": r.error,
                "duration_ms": r.duration_ms,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
            }
            for r in run.step_records
        ],
    }


@app.post("/api/workflows/runs/{run_id}/pause")
async def pause_workflow_run(run_id: str) -> dict:
    """暂停工作流运行"""
    run = await get_workflow_engine().pause(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"run_id": run.id, "status": run.status.value}


@app.post("/api/workflows/runs/{run_id}/resume")
async def resume_workflow_run(run_id: str, yaml_content: str, context: dict | None = None) -> dict:
    """恢复暂停的工作流"""
    try:
        wf = parse_workflow_yaml(yaml_content)
        run = await get_workflow_engine().resume(run_id, wf, context=context)
        return {"run_id": run.id, "status": run.status.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.delete("/api/workflows/runs/{run_id}")
async def delete_workflow_run(run_id: str) -> dict:
    """删除工作流运行记录"""
    _workflow_persistence._delete_run(run_id)
    return {"deleted": run_id}


# ==================== Worker 状态 API ====================

@app.get("/api/workers")
async def list_workers() -> list[dict]:
    """列出所有 Worker"""
    worker_pool = get_worker_pool()
    if not worker_pool:
        return []
    return worker_pool.list_workers()


@app.get("/api/workers/{worker_id}/logs")
async def get_worker_logs(worker_id: str, limit: int = 50) -> dict:
    """获取指定 Worker 的最近日志"""
    worker_pool = get_worker_pool()
    if not worker_pool or worker_id not in worker_pool._workers:
        raise HTTPException(status_code=404, detail="Worker 不存在")
    worker = worker_pool._workers[worker_id]
    return {
        "worker_id": worker_id,
        "logs": worker.get_logs(limit),
    }


@app.delete("/api/workers/{worker_id}/logs")
async def clear_worker_logs(
    worker_id: str,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """清空指定 Worker 的日志（仅管理员）"""
    worker_pool = get_worker_pool()
    if not worker_pool or worker_id not in worker_pool._workers:
        raise HTTPException(status_code=404, detail="Worker 不存在")
    worker_pool._workers[worker_id].clear_logs()
    return {"success": True, "message": "日志已清空"}


@app.get("/api/providers")
async def list_providers() -> list[dict]:
    """列出可用的 Provider 及其模型"""
    # 从 config 中收集每个 provider 已使用过的模型
    provider_models: dict[str, set[str]] = {}
    for template in _config.worker_templates.values():
        provider_models.setdefault(template.provider, set()).add(template.model)
    # coordinator 的模型也加入
    provider_models.setdefault(_config.coordinator.provider, set()).add(_config.coordinator.model)

    # 常用模型补充（让用户有更多选择）
    well_known: dict[str, list[str]] = {
        "anthropic": ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-4-20250506"],
        "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o3-mini"],
        "minimax": ["MiniMax-M1-80k", "MiniMax-M2.7-highspeed"],
        "kimi": ["kimi-coder", "kimi-thinking-coder", "kimi-latest"],
    }

    result = []
    for name in _config.providers:
        models = provider_models.get(name, set())
        for m in well_known.get(name, []):
            models.add(m)
        result.append({"name": name, "models": sorted(models)})
    return result


class WorkerConfigUpdate(BaseModel):
    provider: str
    model: str
    skills: list[str] = []
    tools: list[str] = []
    temperature: float = 0.7
    max_tokens: int = 4096
    icon: str = ""
    display_name: str = ""


@app.put("/api/workers/{worker_id}/config")
async def update_worker_config(
    worker_id: str,
    body: WorkerConfigUpdate,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """更新 Worker 配置并持久化到 config.yaml（仅管理员）"""

    worker_pool = get_worker_pool()
    if not worker_pool or worker_id not in worker_pool._workers:
        raise HTTPException(status_code=404, detail="Worker not found")

    worker = worker_pool._workers[worker_id]
    if worker.is_busy:
        raise HTTPException(status_code=409, detail="Worker 正在执行任务，无法修改配置")

    # 验证 provider 存在
    provider_config = _config.providers.get(body.provider)
    if not provider_config:
        raise HTTPException(status_code=400, detail=f"Provider '{body.provider}' 未配置")

    # 更新运行时 Worker
    worker.config.provider_type = body.provider
    worker.config.model = body.model
    worker.config.skills = body.skills
    worker.config.tools = body.tools

    # 同步刷新 LoadSkillTool 白名单 — 否则新加的 skill 会被当作未启用
    from skills.tool import LoadSkillTool
    if body.skills:
        skills_dir = Path(_config.knowledge_base.skills_dir)
        worker.tools.register(LoadSkillTool(skills_dir, set(body.skills)))
    else:
        worker.tools.unregister("load_skill")
    worker.config.temperature = body.temperature
    worker.config.max_tokens = body.max_tokens
    worker.config.icon = body.icon
    worker.config.display_name = body.display_name
    # 重建 provider 实例
    worker.provider = create_provider(
        body.provider,
        provider_config.resolve_api_key(),
        base_url=provider_config.base_url,
        headers=provider_config.headers,
    )

    # 更新内存中的 config 对象
    if worker_id in _config.worker_templates:
        tpl = _config.worker_templates[worker_id]
        tpl.provider = body.provider
        tpl.model = body.model
        tpl.skills = body.skills
        tpl.tools = body.tools
        tpl.temperature = body.temperature
        tpl.icon = body.icon
        tpl.display_name = body.display_name

    # 持久化到 config.yaml — 只替换目标 worker 块，保留注释和格式
    import re as _re
    config_path = Path("config.yaml")
    text = config_path.read_text(encoding="utf-8")

    # 构建新的 worker 模板 YAML 片段
    skills_yaml = "\n".join(f"    - \"{s}\"" for s in body.skills) if body.skills else "    []"
    tools_yaml = "\n".join(f"    - \"{t}\"" for t in body.tools) if body.tools else "    []"
    icon_line = f"    icon: \"{body.icon}\"\n" if body.icon else ""
    name_line = f"    display_name: \"{body.display_name}\"\n" if body.display_name else ""
    new_block = (
        f"  {worker_id}:\n"
        f"    model: \"{body.model}\"\n"
        f"    provider: \"{body.provider}\"\n"
        f"    temperature: {body.temperature}\n"
        f"{icon_line}"
        f"{name_line}"
        f"    skills:\n{skills_yaml}\n"
        f"    tools:\n{tools_yaml}\n"
    )

    # 匹配 worker_id 块：从 "  worker_id:" 到下一个同级 key 或 section
    pattern = _re.compile(
        rf'^(  {_re.escape(worker_id)}:\n)'   # 块开始
        rf'((?:    .*\n)*)',                     # 缩进 4+ 空格的内容行
        _re.MULTILINE
    )
    new_text, count = pattern.subn(new_block, text, count=1)
    if count > 0:
        config_path.write_text(new_text, encoding="utf-8")

    return {"success": True, "message": f"Worker '{worker_id}' 配置已更新"}


class WorkerCreateRequest(BaseModel):
    name: str
    provider: str
    model: str
    skills: list[str] = []
    tools: list[str] = []
    temperature: float = 0.7
    max_tokens: int = 4096
    icon: str = ""
    display_name: str = ""


@app.post("/api/workers")
async def create_worker(
    body: WorkerCreateRequest,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """新增 Worker 并持久化到 config.yaml（仅管理员）"""
    import re as _re

    worker_pool = get_worker_pool()
    if not worker_pool:
        raise HTTPException(status_code=500, detail="Worker pool not initialized")

    # 名称校验
    name = body.name.strip()
    if not name or not _re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
        raise HTTPException(status_code=400, detail="名称只能包含字母、数字和下划线，且以字母或下划线开头")
    if name in worker_pool._workers:
        raise HTTPException(status_code=409, detail=f"Worker '{name}' 已存在")

    # 验证 provider
    provider_config = _config.providers.get(body.provider)
    if not provider_config:
        raise HTTPException(status_code=400, detail=f"Provider '{body.provider}' 未配置")

    # 创建并注册运行时 Worker
    worker_config = WorkerConfig(
        name=name,
        provider_type=body.provider,
        api_key=provider_config.resolve_api_key(),
        model=body.model,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        tools=body.tools,
        skills=body.skills,
        icon=body.icon,
        display_name=body.display_name,
    )
    worker_provider = create_provider(
        body.provider,
        provider_config.resolve_api_key(),
        base_url=provider_config.base_url,
        headers=provider_config.headers,
    )
    worker_pool.register_worker(WorkerAgent(worker_config, ToolRegistry(), worker_provider))

    # 更新内存 config
    from config import WorkerTemplate
    _config.worker_templates[name] = WorkerTemplate(
        model=body.model,
        provider=body.provider,
        temperature=body.temperature,
        skills=body.skills,
        tools=body.tools,
        icon=body.icon,
        display_name=body.display_name,
    )

    # 持久化 — 在 worker_templates 末尾追加新块
    config_path = Path("config.yaml")
    text = config_path.read_text(encoding="utf-8")
    skills_yaml = "\n".join(f"    - \"{s}\"" for s in body.skills) if body.skills else "    []"
    tools_yaml = "\n".join(f"    - \"{t}\"" for t in body.tools) if body.tools else "    []"
    icon_line = f"    icon: \"{body.icon}\"\n" if body.icon else ""
    name_line = f"    display_name: \"{body.display_name}\"\n" if body.display_name else ""
    new_block = (
        f"\n  {name}:\n"
        f"    model: \"{body.model}\"\n"
        f"    provider: \"{body.provider}\"\n"
        f"    temperature: {body.temperature}\n"
        f"{icon_line}"
        f"{name_line}"
        f"    skills:\n{skills_yaml}\n"
        f"    tools:\n{tools_yaml}\n"
    )
    # 找 worker_templates 节的最后一个条目末尾（下一个顶级 key 之前）
    pattern = _re.compile(r'(worker_templates:.*?)(\n\n# |\n[a-zA-Z_]+:|\Z)', _re.DOTALL)
    match = pattern.search(text)
    if match:
        insert_pos = match.end(1)
        new_text = text[:insert_pos] + new_block + text[insert_pos:]
        config_path.write_text(new_text, encoding="utf-8")

    return {"success": True, "message": f"Worker '{name}' 已创建"}


@app.delete("/api/workers/{worker_id}")
async def delete_worker(
    worker_id: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """删除 Worker 并从 config.yaml 移除（仅管理员）"""
    import re as _re

    worker_pool = get_worker_pool()
    if not worker_pool or worker_id not in worker_pool._workers:
        raise HTTPException(status_code=404, detail="Worker not found")

    worker = worker_pool._workers[worker_id]
    if worker.is_busy:
        raise HTTPException(status_code=409, detail="Worker 正在执行任务，无法删除")

    # 至少保留一个 Worker
    if len(worker_pool._workers) <= 1:
        raise HTTPException(status_code=400, detail="至少需要保留一个 Worker")

    # 从运行时移除
    worker_pool.unregister_worker(worker_id)

    # 从内存 config 移除
    _config.worker_templates.pop(worker_id, None)

    # 从 config.yaml 移除
    config_path = Path("config.yaml")
    text = config_path.read_text(encoding="utf-8")
    pattern = _re.compile(
        rf'^\n?  {_re.escape(worker_id)}:\n'
        rf'((?:    .*\n)*)',
        _re.MULTILINE
    )
    new_text = pattern.sub('', text, count=1)
    if new_text != text:
        config_path.write_text(new_text, encoding="utf-8")

    _audit_log(request, user, "delete", "worker", worker_id)
    return {"success": True, "message": f"Worker '{worker_id}' 已删除"}


# ==================== Skills API ====================

@app.get("/api/skills")
async def list_installed_skills() -> dict:
    """列出已安装 skill。"""
    from skills.loader import list_skills as _list_skills
    skills_dir = Path(_config.knowledge_base.skills_dir)
    out = []
    for s in _list_skills(skills_dir):
        source_url = ""
        meta_path = s.path / ".install.json"
        if meta_path.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                source_url = json.loads(meta_path.read_text(encoding="utf-8")).get("source_url", "")
        out.append({
            "name": s.name,
            "description": s.description,
            "source_url": source_url,
        })
    return {"skills": out}


@app.get("/api/skills/search")
async def search_skills(q: str = "", limit: int = 10) -> dict:
    """在 registry 里按关键字+向量混合搜索 skill。已安装的会被排除。

    当 registry 中有预计算的 embedding 时，使用 keyword score (0-1) +
    cosine similarity (0-1) 混合评分，各占 50%。
    结果会尽力附带 GitHub stars + pushed_at(本地 24h TTL 缓存,首次冷启会拉网);
    若元数据未就绪(缓存没命中且抓取失败/超时),对应字段缺省,前端需容忍缺失。
    """
    from skills.github_meta import enrich_with_repo_meta
    from skills.loader import list_skills as _list_skills
    from skills.registry import load_registry, search_registry

    skills_dir = Path(_config.knowledge_base.skills_dir)
    registry_path = Path("data/skills_registry.json")
    meta_cache_path = Path("data/github_meta_cache.json")

    installed = {s.name for s in _list_skills(skills_dir)}
    entries = load_registry(registry_path)

    # Embedding-based search when query is not empty and rag_engine is available
    query_embedding: list[float] | None = None
    if q and _rag_engine is not None and _rag_engine.vector_store is not None:
        try:
            emb_fn = _rag_engine.vector_store.embedding_function
            if emb_fn is not None:
                results_emb = await emb_fn.embed([q])
                query_embedding = results_emb[0]
        except Exception:
            pass  # Fall back to keyword-only search

    results = search_registry(entries, q, installed, limit=limit, query_embedding=query_embedding)

    meta = await enrich_with_repo_meta([r.source_url for r in results], meta_cache_path)

    return {
        "query": q,
        "results": [
            {
                "name": r.name,
                "description": r.description,
                "source_url": r.source_url,
                "score": r.score,
                "stars": meta.get(r.source_url, {}).get("stars"),
                "pushed_at": meta.get(r.source_url, {}).get("pushed_at"),
                # wiki-style frontmatter fields
                "created": r.created,
                "updated": r.updated,
                "tags": r.tags,
                "sources": r.sources,
                "contradictions": r.contradictions,
                "contested": r.contested,
            }
            for r in results
        ],
    }


class SkillInstallRequest(BaseModel):
    source_url: str
    name: str | None = None
    force: bool = False


@app.post("/api/skills/install")
async def install_skill(body: SkillInstallRequest):
    """从 GitHub 安装 skill,以 SSE 流推送阶段进度。"""
    from skills.installer import install_from_github

    skills_dir = Path(_config.knowledge_base.skills_dir)
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_progress(stage: str, msg: str) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"stage": stage, "message": msg},
        )

    async def worker() -> None:
        try:
            skill = await asyncio.to_thread(
                install_from_github,
                body.source_url,
                skills_dir,
                body.name,
                body.force,
                on_progress,
            )
            # 回写 registry — 让下次搜索直接命中
            try:
                from skills.registry import upsert_registry_entry
                changed, action = upsert_registry_entry(
                    Path("data/skills_registry.json"),
                    name=skill.name,
                    description=skill.description,
                    source_url=body.source_url,
                )
                if changed:
                    await queue.put({"stage": "registry", "message": f"已登记到 registry ({action})"})
                else:
                    await queue.put({"stage": "registry", "message": "registry 内容无变化"})
            except Exception as e:  # noqa: BLE001
                await queue.put({"stage": "registry", "message": f"registry 写入失败: {e}"})
            await queue.put({
                "stage": "success",
                "name": skill.name,
                "description": skill.description,
            })
        except FileExistsError as e:
            await queue.put({"stage": "error", "code": "exists", "message": str(e)})
        except FileNotFoundError as e:
            await queue.put({"stage": "error", "code": "not_found", "message": str(e)})
        except (ValueError, RuntimeError) as e:
            await queue.put({"stage": "error", "code": "invalid", "message": str(e)})
        except Exception as e:  # noqa: BLE001
            await queue.put({"stage": "error", "code": "unknown", "message": str(e)})
        finally:
            await queue.put(None)

    async def event_stream():
        asyncio.create_task(worker())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/skills/rebuild-embeddings")
async def rebuild_skill_embeddings(
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """预计算所有 skill 的描述向量（仅管理员）。用于首次启用向量搜索。

    过程: 遍历 skills_registry.json 中无 embedding 的 entry，
    调用 RAG 同款 embedding function 补全 embedding 字段。
    后续新增/更新 skill 时自动补全，无需重复调用。
    """
    if _rag_engine is None or _rag_engine.vector_store is None:
        raise HTTPException(status_code=503, detail="RAG engine 未初始化")

    emb_fn = _rag_engine.vector_store.embedding_function
    if emb_fn is None:
        raise HTTPException(status_code=503, detail="embedding function 不可用")

    registry_path = Path("data/skills_registry.json")
    if not registry_path.is_file():
        raise HTTPException(status_code=404, detail="skills_registry.json 不存在")

    from skills.registry import rebuild_embeddings
    count = await rebuild_embeddings(registry_path, emb_fn)
    return {"computed": count, "message": f"已为 {count} 个 skill 计算向量"}


# ── wiki-style registry lint / log / tags ────────────────────────────────────


@app.get("/api/skills/log")
async def get_skill_log(limit: int = 20) -> dict:
    """返回 registry 变更日志（append-only），最新在前。"""
    registry_path = Path("data/skills_registry.json")
    if not registry_path.is_file():
        raise HTTPException(status_code=404, detail="skills_registry.json 不存在")
    from skills.registry import get_change_log
    log = get_change_log(registry_path, limit=limit)
    return {"log": log, "count": len(log)}


@app.get("/api/skills/contested")
async def get_contested_skills() -> dict:
    """返回所有标记了 contested 的 entry（存在未解决冲突）。"""
    registry_path = Path("data/skills_registry.json")
    if not registry_path.is_file():
        raise HTTPException(status_code=404, detail="skills_registry.json 不存在")
    from skills.registry import get_contested, load_registry
    entries = get_contested(registry_path)
    return {
        "contested": [
            {
                "name": e.name,
                "description": e.description,
                "contradictions": e.contradictions,
                "updated": e.updated,
            }
            for e in entries
        ],
        "count": len(entries),
    }


@app.get("/api/skills/tags")
async def get_all_skill_tags() -> dict:
    """返回 registry 中所有已使用的标签（去重、排序）。"""
    registry_path = Path("data/skills_registry.json")
    if not registry_path.is_file():
        raise HTTPException(status_code=404, detail="skills_registry.json 不存在")
    from skills.registry import get_all_tags
    tags = get_all_tags(registry_path)
    return {"tags": tags, "count": len(tags)}


class SkillLintRequest(BaseModel):
    action: Literal["upsert", "resolve"]  # noqa: N815
    name: str
    contradictions: list[str] = []
    contested: bool = False


@app.post("/api/skills/lint")
async def lint_skill_registry(
    body: SkillLintRequest,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """手动更新 skill 的冲突标记（仅管理员）。

    action=upsert:   对已有 entry 更新 contradictions / contested 字段。
    action=resolve:  清除 contested 标记，表示冲突已人工审查并解决。
    """
    registry_path = Path("data/skills_registry.json")
    if not registry_path.is_file():
        raise HTTPException(status_code=404, detail="skills_registry.json 不存在")

    from skills.registry import load_registry

    data = json.loads(registry_path.read_text(encoding="utf-8"))
    entries: list[dict] = data.get("skills", [])

    for e in entries:
        if e["name"] == body.name:
            if body.action == "upsert":
                e["contradictions"] = body.contradictions
                e["contested"] = body.contested
                _append_log(data, "conflict_mark", body.name, f"contested={body.contested}")
            elif body.action == "resolve":
                e["contested"] = False
                _append_log(data, "resolve", body.name, "")
            registry_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return {"ok": True, "name": body.name, "action": body.action}
    raise HTTPException(status_code=404, detail=f"skill '{body.name}' not found in registry")


def _append_log(data: dict, action: str, name: str, note: str = "") -> None:
    """Append to the in-memory log list within data (used by lint endpoint)."""
    from datetime import datetime
    log: list[dict] = data.setdefault("log", [])
    entry = {
        "action": action,
        "name": name,
        "at": datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }
    if len(log) >= 500:
        log[:] = log[-499:]
    log.append(entry)


@app.delete("/api/skills/{name}")
async def uninstall_skill(
    name: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """卸载已安装 skill（仅管理员）。"""
    from skills.installer import remove_skill as _remove
    skills_dir = Path(_config.knowledge_base.skills_dir)
    try:
        _remove(skills_dir, name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    _audit_log(request, user, "uninstall", "skill", name)
    return {"success": True, "name": name}


# ==================== 系统 API ====================

@app.get("/api/health")
async def health_check() -> dict:
    """健康检查"""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "documents": len(_rag_engine.list_documents()) if _rag_engine else 0,
        "workers": len(get_worker_pool().list_workers()) if get_worker_pool() else 0,
    }


# ==================== WebSocket ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 实时通信"""
    await websocket.accept()
    _ws_connections.add(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            msg_type = message.get("type")

            if msg_type == "chat":
                # 流式聊天
                session_id = message.get("session_id", str(uuid.uuid4())[:8])
                user_message = message.get("message", "")

                # RAG 检索
                search_results = []
                if _rag_engine:
                    search_results = await _rag_engine.search(user_message)

                    await websocket.send_json({
                        "type": "sources",
                        "data": [
                            {"filename": r.metadata.get("filename", "unknown"), "score": r.score}
                            for r in search_results
                        ],
                    })

                # 构建提示
                if search_results and _rag_engine:
                    messages = _rag_engine.build_rag_prompt(user_message, search_results)
                else:
                    messages = [
                        {"role": "system", "content": "你是一个智能助手。"},
                        {"role": "user", "content": user_message},
                    ]

                # 调用 LLM
                coordinator_provider_config = _config.providers.get(_config.coordinator.provider)
                if coordinator_provider_config:
                    provider = create_provider(
                        _config.coordinator.provider,
                        coordinator_provider_config.resolve_api_key(),
                        base_url=coordinator_provider_config.base_url,
                        headers=coordinator_provider_config.headers,
                    )

                    try:
                        response = await provider.chat_stream(
                            messages=messages,
                            model=_config.coordinator.model,
                        )

                        # 流式发送
                        for i in range(0, len(response.content or ""), 10):
                            chunk = response.content[i:i+10]
                            await websocket.send_json({"type": "chunk", "content": chunk})
                            await asyncio.sleep(0.02)

                        await websocket.send_json({
                            "type": "done",
                            "session_id": session_id,
                            "content": response.content,
                        })

                    except Exception as e:
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e),
                        })

            elif msg_type == "task_progress":
                # 任务进度
                await websocket.send_json({
                    "type": "task_progress",
                    "data": message.get("data"),
                })

    except WebSocketDisconnect:
        _ws_connections.discard(websocket)
    except Exception as e:
        _ws_connections.discard(websocket)
        with contextlib.suppress(BaseException):
            await websocket.send_json({"type": "error", "message": str(e)})


# ==================== 前端静态文件 ====================

_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"

if _frontend_dist.exists():
    # 挂载静态资源（JS/CSS/图片等）
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str, request: Request):
        """所有非 /api 路径返回前端 index.html（支持前端路由）"""
        # API 路径不走这里（FastAPI 路由优先级更高）
        file_path = _frontend_dist / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(
            str(_frontend_dist / "index.html"),
            headers={"Cache-Control": "no-cache"},
        )


# ==================== 导出 ====================

def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    return app
