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
_group_store: GroupStore | None = None

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


class URLRequest(BaseModel):
    """网页 URL 导入请求"""
    url: str


class TaskRequest(BaseModel):
    """任务请求"""
    description: str
    context: dict | None = None
    generate_suggestions: bool = True
    active_group_ids: list[str] | None = None


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
    
    # 初始化 RAG 引擎
    _rag_engine = init_rag_engine(
        persist_directory=kb_config.persist_directory,
        embedding_function=embedding_function,
        chunk_size=kb_config.chunk_size,
        chunk_overlap=kb_config.chunk_overlap,
        top_k=kb_config.top_k,
    )
    
    # 预热嵌入模型（避免首次请求时延迟）
    print("   - 预热嵌入模型...")
    await _rag_engine.vector_store.embedding_function.embed(["预热模型"])
    
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
            _rag_engine.add_document(file_path, group_id=group_id),
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


# ==================== RAG 问答 API ====================

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

    # 调用 LLM
    coordinator_provider_config = _config.providers.get(_config.coordinator.provider)
    if not coordinator_provider_config:
        raise HTTPException(status_code=500, detail="LLM provider not configured")

    api_key = coordinator_provider_config.resolve_api_key()
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail=f"LLM API Key 未配置（provider: {_config.coordinator.provider}），请在 config.yaml 或环境变量中设置",
        )

    provider = create_provider(
        _config.coordinator.provider,
        api_key,
    )

    try:
        response = await provider.chat(
            messages=messages,
            model=_config.coordinator.model,
            temperature=_config.coordinator.temperature,
            max_tokens=_config.coordinator.max_tokens,
        )
    except Exception as e:
        import httpx as _httpx
        if isinstance(e, _httpx.HTTPStatusError):
            status = e.response.status_code
            if status == 401:
                raise HTTPException(status_code=502, detail=f"LLM API Key 无效或已过期（{_config.coordinator.provider}）")
            elif status == 429:
                raise HTTPException(status_code=429, detail="LLM API 请求频率超限，请稍后重试")
            else:
                raise HTTPException(status_code=502, detail=f"LLM 服务返回错误 {status}")
        raise HTTPException(status_code=502, detail=f"LLM 调用失败: {type(e).__name__}: {str(e)}")

    answer = response.content or "抱歉，我无法回答这个问题。"
    
    # 添加助手消息
    _rag_engine.add_message(session_id, "assistant", answer)
    
    return {
        "session_id": session_id,
        "answer": answer,
        "sources": [
            {
                "content": r.content[:200] + "..." if len(r.content) > 200 else r.content,
                "score": r.score,
                "filename": r.metadata.get("filename", "unknown"),
            }
            for r in search_results
        ],
    }


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
        
        # 流式调用 LLM
        coordinator_provider_config = _config.providers.get(_config.coordinator.provider)
        if not coordinator_provider_config:
            yield f"data: {json.dumps({'type': 'error', 'message': 'LLM provider not configured'})}\n\n"
            return
        
        provider = create_provider(
            _config.coordinator.provider,
            coordinator_provider_config.resolve_api_key(),
        )
        
        content_parts: list[str] = []
        
        async def on_chunk(chunk: str):
            content_parts.append(chunk)
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
        
        try:
            response = await provider.chat_stream(
                messages=messages,
                model=_config.coordinator.model,
                temperature=_config.coordinator.temperature,
                max_tokens=_config.coordinator.max_tokens,
                on_chunk=lambda c: None,  # 暂时忽略回调
            )
            
            # 直接发送完整内容作为流
            for i in range(0, len(response.content or ""), 10):
                chunk = response.content[i:i+10]
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
                await asyncio.sleep(0.02)
            
            # 发送完成
            answer = "".join(content_parts) or response.content or ""
            _rag_engine.add_message(session_id, "assistant", answer)
            
            yield f"data: {json.dumps({'type': 'done', 'session_id': session_id})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")


# ==================== 任务执行 API ====================

@app.post("/api/tasks")
async def create_task(request: TaskRequest) -> dict:
    """创建并执行任务"""
    if not _task_planner:
        raise HTTPException(status_code=500, detail="Task planner not initialized")
    
    # 规划任务
    task, complexity = await _task_planner.plan_task(request.description, request.context)
    
    # 执行任务
    result = await _task_planner.execute_task(task, request.context)
    
    # 生成优化建议
    suggestions = []
    if request.generate_suggestions:
        suggestions = await _task_planner.generate_optimization_suggestions(task, result, request.context)
    
    return {
        "task_id": task.id,
        "complexity": complexity.value,
        "result": result,
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


@app.get("/api/tasks")
async def list_tasks() -> list[dict]:
    """列出所有任务"""
    if not _task_planner:
        return []
    return _task_planner.list_tasks()


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    """获取任务详情"""
    if not _task_planner:
        raise HTTPException(status_code=500, detail="Task planner not initialized")
    
    task = _task_planner.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
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


# ==================== Worker 状态 API ====================

@app.get("/api/workers")
async def list_workers() -> list[dict]:
    """列出所有 Worker"""
    worker_pool = get_worker_pool()
    if not worker_pool:
        return []
    return worker_pool.list_workers()


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
        pass
    except Exception as e:
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
        return FileResponse(str(_frontend_dist / "index.html"))


# ==================== 导出 ====================

def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    return app
