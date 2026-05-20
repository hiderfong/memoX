"""Web API - FastAPI 服务"""

import asyncio
import contextlib
import json
import re as _re
import sys
import uuid
from pathlib import Path

from loguru import logger

# 添加 src 目录到路径
_src_dir = Path(__file__).parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

# The app can be imported as either ``src.web.api`` or ``web.api`` depending on
# how uvicorn/tests are started. Keep both names bound to this module so routers
# that lazily read globals do not see a second, uninitialized module instance.
_this_module = sys.modules[__name__]
sys.modules["web.api"] = _this_module
sys.modules["src.web.api"] = _this_module

from typing import Literal  # noqa: E402, I001

from fastapi import (  # noqa: E402
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from slowapi import Limiter  # noqa: E402
from slowapi.errors import RateLimitExceeded as RLError  # noqa: E402
from slowapi.util import get_remote_address  # noqa: E402

from agents.base_agent import ToolRegistry, create_provider  # noqa: E402
from agents.worker_pool import WorkerAgent, WorkerConfig, get_worker_pool, init_worker_pool  # noqa: E402
from auth import AuthUser, _get_auth_from_request, init_auth  # noqa: E402
from config import Config, default_config_path, load_config, resolve_env_value, validate_config  # noqa: E402
from coordinator.iterative_orchestrator import IterativeOrchestrator  # noqa: E402
from coordinator.task_planner import TaskPlanner, init_task_planner  # noqa: E402
from memory import MemoryManager, MemoryRecall, PreferenceLearner  # noqa: E402
from knowledge import (  # noqa: E402
    RAGEngine,
    init_rag_engine,
)
from knowledge.document_parser import WebPageParser  # noqa: E402, F401
from knowledge.group_store import GroupStore  # noqa: E402
from knowledge.vector_store import (  # noqa: E402
    DashScopeEmbedding,
    HashEmbedding,
    OpenAIEmbedding,
    SentenceTransformerEmbedding,
)
from storage import get_store, init_store  # noqa: E402
from workflow import (  # noqa: E402
    WorkflowEngine,
    WorkflowPersistence,
)


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


@contextlib.asynccontextmanager
async def lifespan(app_: FastAPI):
    await startup()
    yield


app = FastAPI(
    title="MemoX API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

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

# ── Upload limits ──────────────────────────────────────────────
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB per file

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
_workflow_engine: WorkflowEngine | None = None
_workflow_persistence: WorkflowPersistence | None = None
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
    "/api/redoc",
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

async def startup():
    """启动时初始化"""
    global _config, _rag_engine, _task_planner

    # 加载配置
    _config = load_config()
    validate_config(_config)

    # 初始化认证
    if _config.auth.enabled:
        init_auth(
            [
                {
                    "username": u.username,
                    "password": resolve_env_value(u.password),
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

    if embedding_provider in ("hash", "local-hash"):
        embedding_function = HashEmbedding()
        logger.info("   - 使用本地 Hash Embedding（仅适合 smoke/demo）")
    elif embedding_provider == "dashscope":
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
    workflow_db_path = Path(_config.knowledge_base.persist_directory).parent / "workflows.db"
    global _workflow_persistence, _workflow_engine
    _workflow_persistence = WorkflowPersistence(str(workflow_db_path))

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
        _workflow_engine = WorkflowEngine(worker_pool, coordinator_provider, _workflow_persistence)

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

    # 启动运维维护任务：自动备份与本地归档裁剪
    if _config.ops.auto_backup_enabled:
        from ops.maintenance import init_maintenance_runner

        config_path = default_config_path()
        if not config_path.is_absolute():
            config_path = Path.cwd() / config_path
        ops_runner = init_maintenance_runner(
            root=config_path.resolve().parent,
            include=tuple(_config.ops.auto_backup_include),
            interval_hours=_config.ops.auto_backup_interval_hours,
            startup_delay_seconds=_config.ops.auto_backup_startup_delay_seconds,
            max_backups=_config.ops.max_backups,
        )
        ops_runner.start()

    # 注册优雅停机
    import atexit
    def _stop_background_runners():
        from scheduler import get_runner
        r = get_runner()
        if r:
            r.stop()
        from ops.maintenance import get_maintenance_runner
        ops_runner = get_maintenance_runner()
        if ops_runner:
            ops_runner.stop()
    atexit.register(_stop_background_runners)

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


# ── Router imports ─────────────────────────────────────────────────────────
from web.routers import (  # noqa: E402
    auth_router,
    chat_router,
    documents_router,
    imaging_router,
    memories_router,
    scheduled_router,
    skills_router,
    system_router,
    tasks_router,
    workers_router,
    workflows_router,
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(documents_router)
app.include_router(imaging_router)
app.include_router(memories_router)
app.include_router(scheduled_router)
app.include_router(skills_router)
app.include_router(system_router)
app.include_router(tasks_router)
app.include_router(workers_router)
app.include_router(workflows_router)



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


@app.get("/api/files/{name:path}")
async def serve_upload(name: str, request: Request):
    """暴露 data/uploads/ 下的文件（供 DashScope 拉取图片等场景）"""
    raw_name = request.url.path.removeprefix("/api/files/")
    if "/" in raw_name or "\\" in raw_name or ".." in raw_name:
        raise HTTPException(status_code=400, detail="非法文件名")
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
