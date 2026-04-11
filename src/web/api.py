"""Web API - FastAPI 服务"""

import os
import sys
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

# 添加 src 目录到路径
_src_dir = Path(__file__).parent.parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import load_config, Config
from dataclasses import asdict
from knowledge.group_store import GroupStore, UNGROUPED_ID
from auth import init_auth, get_auth_manager
from knowledge.document_parser import WebPageParser
from knowledge import (
    RAGEngine,
    init_rag_engine,
    get_rag_engine,
    DocumentInfo,
    SearchResult,
)
from knowledge.vector_store import (
    SentenceTransformerEmbedding,
    OpenAIEmbedding,
    DashScopeEmbedding,
)
from agents.base_agent import create_provider, AnthropicProvider, ToolRegistry
from agents.worker_pool import init_worker_pool, get_worker_pool, WorkerConfig, WorkerAgent
from coordinator.task_planner import TaskPlanner, init_task_planner
from coordinator.iterative_orchestrator import IterativeOrchestrator, IterationResult
from storage import init_store, get_store


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

_config: Config | None = None
_rag_engine: RAGEngine | None = None
_task_planner: TaskPlanner | None = None
_orchestrator: IterativeOrchestrator | None = None
_group_store: GroupStore | None = None
_task_results: dict[str, dict] = {}  # task_id -> full result dict (for later retrieval)
_ws_connections: set[WebSocket] = set()


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
    if path in _PUBLIC_PATHS:
        return await call_next(request)

    # WebSocket 路径：token 通过 query param 传递
    if path == "/ws":
        token = request.query_params.get("token", "")
    else:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()

    auth = get_auth_manager()
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
        init_auth([
            {
                "username": u.username,
                "password": u.password,
                "role": u.role,
                "display_name": u.display_name,
            }
            for u in _config.auth.users
        ])
        _PUBLIC_PATHS.update(_config.auth.public_paths)
        print(f"   - 认证已启用，{len(_config.auth.users)} 个用户")
    
    # CORS 已在创建 app 时配置
    pass
    
    # 根据配置创建 Embedding Function
    kb_config = _config.knowledge_base
    embedding_provider = kb_config.embedding_provider
    
    if embedding_provider == "dashscope":
        # 阿里云 DashScope
        dashscope_config = _config.providers.get("dashscope")
        if dashscope_config and dashscope_config.api_key:
            embedding_function = DashScopeEmbedding(
                api_key=dashscope_config.api_key,
                model=kb_config.embedding_model or "text-embedding-v3"
            )
            print(f"   - 使用 DashScope Embedding: {kb_config.embedding_model}")
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
            print(f"   - 使用 OpenAI Embedding: {kb_config.embedding_model}")
        else:
            raise ValueError("OpenAI API key not configured")
    else:
        # 默认本地 Sentence Transformer
        embedding_function = SentenceTransformerEmbedding(
            model_name=kb_config.embedding_model or "sentence-transformers/all-MiniLM-L6-v2"
        )
        print(f"   - 使用本地 Embedding: {kb_config.embedding_model}")
    
    # DashScope config for image OCR
    dashscope_config = _config.providers.get("dashscope")
    dashscope_api_key = dashscope_config.api_key if dashscope_config else ""
    dashscope_base_url = (dashscope_config.base_url if dashscope_config else "").replace("/api/v1", "/compatible-mode/v1")

    # 初始化 RAG 引擎
    _rag_engine = init_rag_engine(
        persist_directory=kb_config.persist_directory,
        embedding_function=embedding_function,
        chunk_size=kb_config.chunk_size,
        chunk_overlap=kb_config.chunk_overlap,
        top_k=kb_config.top_k,
        dashscope_api_key=dashscope_api_key,
        dashscope_base_url=dashscope_base_url,
    )
    
    # 预热嵌入模型（避免首次请求时延迟）
    print("   - 预热嵌入模型...")
    await _rag_engine.vector_store.embedding_function.embed(["预热模型"])

    # 初始化持久化存储（需要在 Worker 创建之前，以便 Worker 从 DB 恢复 token 用量）
    db_path = Path(_config.knowledge_base.persist_directory).parent / "memox.db"
    init_store(db_path)
    print(f"   - 持久化存储: {db_path}")

    # 初始化 Worker 池
    worker_pool = init_worker_pool(max_workers=_config.coordinator.max_workers)
    
    # 注册 Worker（从模板）
    for name, template in _config.worker_templates.items():
        provider_config = _config.providers.get(template.provider)
        if provider_config:
            worker_provider = create_provider(
                template.provider,
                provider_config.resolve_api_key(),
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
        print(f"   - 迁移 {migrated} 个历史 chunk，补写 group_id=ungrouped")

    print(f"✅ MemoX 启动完成")
    print(f"   - RAG 引擎: {len(_rag_engine.list_documents())} 个文档")


# ==================== 认证 API ====================

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def login(request: LoginRequest) -> dict:
    """用户登录，返回 Bearer Token"""
    auth = get_auth_manager()
    token = auth.login(request.username, request.password)
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
    auth = get_auth_manager()
    if not token or not auth.validate_token(token):
        raise HTTPException(status_code=401, detail="未登录或 Token 已过期")
    auth.logout(token)
    return {"success": True}


@app.get("/api/auth/me")
async def me(request: Request) -> dict:
    """获取当前登录用户信息"""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user_info = get_auth_manager().get_user_info(token)
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
async def upload_document(
    file: UploadFile = File(...),
    group_id: str = Form(default="ungrouped"),
) -> DocumentResponse:
    """上传文档"""
    import traceback
    import asyncio
    
    upload_dir = Path(_config.knowledge_base.upload_directory)
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存文件
    file_path = upload_dir / f"{uuid.uuid4().hex}_{file.filename}"
    try:
        content = await file.read()
        file_path.write_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件保存失败: {str(e)}")
    
    print(f"[UPLOAD] File saved: {file_path}, size: {len(content)} bytes")
    
    # 添加到知识库（带整体超时）
    try:
        # 总超时 300 秒（5分钟），给大文件处理足够时间
        doc_info = await asyncio.wait_for(
            _rag_engine.add_document(file_path, group_id=group_id, original_filename=file.filename),
            timeout=300.0
        )
        print(f"[UPLOAD] Document added: {doc_info.id}")
        return DocumentResponse(
            id=doc_info.id,
            filename=doc_info.filename,
            type=doc_info.type,
            chunk_count=doc_info.chunk_count,
            created_at=doc_info.created_at,
            size=doc_info.size,
            group_id=doc_info.group_id,
        )
    except asyncio.TimeoutError:
        print(f"[UPLOAD ERROR] 文档处理超时")
        # 清理文件
        try:
            file_path.unlink(missing_ok=True)
        except:
            pass
        raise HTTPException(status_code=504, detail="文档处理超时，请尝试上传更小的文件或简化文档格式")
    except TimeoutError as e:
        print(f"[UPLOAD ERROR] {e}")
        try:
            file_path.unlink(missing_ok=True)
        except:
            pass
        raise HTTPException(status_code=504, detail=str(e))
    except ValueError as e:
        print(f"[UPLOAD ERROR] {e}")
        try:
            file_path.unlink(missing_ok=True)
        except:
            pass
        raise HTTPException(status_code=413, detail=str(e))
    except Exception as e:
        print(f"[UPLOAD ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        # 清理文件
        try:
            file_path.unlink(missing_ok=True)
        except:
            pass
        raise HTTPException(status_code=500, detail=f"文档处理失败: {type(e).__name__}: {str(e)}")


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
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="网页抓取超时")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"网页抓取失败: {type(e).__name__}: {str(e)}")

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
async def delete_document(doc_id: str) -> dict:
    """删除文档"""
    success = await _rag_engine.delete_document(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
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
async def update_group(group_id: str, request: GroupUpdate) -> dict:
    """修改分组名称或颜色"""
    try:
        group = _group_store.update_group(group_id, request.name, request.color)
        return {"id": group.id, "name": group.name, "color": group.color, "created_at": group.created_at}
    except KeyError:
        raise HTTPException(status_code=404, detail="Group not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: str) -> dict:
    """删除分组，其下文档自动归回未分组"""
    try:
        docs = _rag_engine.list_documents()
        for doc in docs:
            if doc.group_id == group_id:
                _rag_engine.move_document_group(doc.id, UNGROUPED_ID)
        _group_store.delete_group(group_id)
        return {"success": True}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/documents/{doc_id}/group")
async def move_document_group(doc_id: str, request: MoveDocumentGroup) -> dict:
    """修改文档所属分组"""
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
            provider = create_provider(w.config.provider_type, pcfg.resolve_api_key())
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
    provider = create_provider(_config.coordinator.provider, api_key)
    return provider, _config.coordinator.model, _config.coordinator.temperature, _config.coordinator.max_tokens, None


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict:
    """聊天问答（非流式）"""
    session_id = request.session_id or str(uuid.uuid4())[:8]
    
    # 获取或创建会话
    session = _rag_engine.get_session(session_id)
    if not session:
        session = _rag_engine.create_session()
    
    # 添加用户消息
    _rag_engine.add_message(session_id, "user", request.message)
    
    # RAG 检索
    search_results: list[SearchResult] = []
    if request.use_rag:
        search_results = await _rag_engine.search(request.message, group_ids=request.active_group_ids)

    # 构建提示
    if search_results:
        messages = _rag_engine.build_rag_prompt(request.message, search_results)
    else:
        messages = [
            {"role": "system", "content": "你是一个智能助手。"},
            {"role": "user", "content": request.message},
        ]

    # 调用 LLM（根据 worker_id 选择模型配置）
    provider, model, temperature, max_tokens, resolved_worker_id = _resolve_chat_llm(request.worker_id)

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
                raise HTTPException(status_code=502, detail="LLM API Key 无效或已过期")
            elif status == 429:
                raise HTTPException(status_code=429, detail="LLM API 请求频率超限，请稍后重试")
            else:
                raise HTTPException(status_code=502, detail=f"LLM 服务返回错误 {status}")
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {type(e).__name__}: {str(e)}")

    answer = response.content or "抱歉，我无法回答这个问题。"

    # 添加助手消息
    _rag_engine.add_message(session_id, "assistant", answer)

    # 持久化消息
    store = get_store()
    if store:
        store.save_message(session_id, "user", request.message)
        store.save_message(session_id, "assistant", answer)
        # 自动生成会话标题（取用户第一条消息前 30 字）
        existing = store.get_session_messages(session_id)
        if len(existing) <= 2:  # 第一轮对话
            title = request.message[:30].strip()
            store.update_session_title(session_id, title)

    return {
        "session_id": session_id,
        "answer": answer,
        "worker_id": resolved_worker_id,
        "sources": [
            {
                "content": r.content[:200] + "..." if len(r.content) > 200 else r.content,
                "score": r.score,
                "filename": r.metadata.get("filename", "unknown"),
            }
            for r in search_results
        ],
    }


@app.get("/api/chat/sessions")
async def list_chat_sessions() -> list[dict]:
    """列出聊天会话历史"""
    store = get_store()
    if store:
        return store.list_sessions()
    return []


@app.get("/api/chat/sessions/{session_id}/messages")
async def get_session_messages(session_id: str) -> list[dict]:
    """获取会话消息历史"""
    store = get_store()
    if store:
        messages = store.get_session_messages(session_id)
        if messages:
            return messages
    raise HTTPException(status_code=404, detail="Session not found")


@app.delete("/api/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str) -> dict:
    """删除聊天会话"""
    store = get_store()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    if not store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    # 同时清理 RAG 引擎内存中的会话
    if _rag_engine:
        _rag_engine.delete_session(session_id)
    return {"success": True}


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """流式聊天问答"""
    session_id = request.session_id or str(uuid.uuid4())[:8]
    
    async def generate():
        # 获取或创建会话
        session = _rag_engine.get_session(session_id)
        if not session:
            session = _rag_engine.create_session()
        
        # 添加用户消息
        _rag_engine.add_message(session_id, "user", request.message)
        
        # RAG 检索
        search_results: list[SearchResult] = []
        if request.use_rag:
            search_results = await _rag_engine.search(request.message, group_ids=request.active_group_ids)

            # 先发送检索结果
            yield f"data: {json.dumps({'type': 'sources', 'data': [{'filename': r.metadata.get('filename', 'unknown'), 'score': r.score} for r in search_results]})}\n\n"
        
        # 构建提示
        if search_results:
            messages = _rag_engine.build_rag_prompt(request.message, search_results)
        else:
            messages = [
                {"role": "system", "content": "你是一个智能助手。"},
                {"role": "user", "content": request.message},
            ]
        
        # 流式调用 LLM（根据 worker_id 选择模型配置）
        try:
            provider, model, temperature, max_tokens, resolved_worker_id = _resolve_chat_llm(request.worker_id)
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
            
            # 直接发送完整内容作为流
            for i in range(0, len(response.content or ""), 10):
                chunk = response.content[i:i+10]
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
                await asyncio.sleep(0.02)
            
            # 发送完成
            answer = response.content or ""
            _rag_engine.add_message(session_id, "assistant", answer)

            # 持久化消息
            store = get_store()
            if store:
                store.save_message(session_id, "user", request.message)
                store.save_message(session_id, "assistant", answer)
                # 自动生成会话标题
                existing = store.get_session_messages(session_id)
                if len(existing) <= 2:
                    title = request.message[:30].strip()
                    store.update_session_title(session_id, title)

            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'worker_id': resolved_worker_id})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")


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
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"任务执行超时（{timeout}秒）")

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



# ==================== Worker 状态 API ====================

@app.get("/api/workers")
async def list_workers() -> list[dict]:
    """列出所有 Worker"""
    worker_pool = get_worker_pool()
    if not worker_pool:
        return []
    return worker_pool.list_workers()


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
async def update_worker_config(worker_id: str, body: WorkerConfigUpdate) -> dict:
    """更新 Worker 配置并持久化到 config.yaml"""
    import yaml as _yaml

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
    worker.config.temperature = body.temperature
    worker.config.max_tokens = body.max_tokens
    worker.config.icon = body.icon
    worker.config.display_name = body.display_name
    # 重建 provider 实例
    worker.provider = create_provider(body.provider, provider_config.resolve_api_key())

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
async def create_worker(body: WorkerCreateRequest) -> dict:
    """新增 Worker 并持久化到 config.yaml"""
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
    worker_provider = create_provider(body.provider, provider_config.resolve_api_key())
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
async def delete_worker(worker_id: str) -> dict:
    """删除 Worker 并从 config.yaml 移除"""
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

    return {"success": True, "message": f"Worker '{worker_id}' 已删除"}


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
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass


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
