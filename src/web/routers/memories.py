"""Memories router — cross-session persistent memory"""
import contextlib
import json as _json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth import AuthUser, get_current_user

router = APIRouter(prefix="/api/memories", tags=["memories"])

# ── Model classes ──────────────────────────────────────────────────────────

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


# ── Helpers ────────────────────────────────────────────────────────────────

def _get_globals():
    import web.api as _api_module
    return (
        getattr(_api_module, "_memory_recall"),
        getattr(_api_module, "_rag_engine"),
        getattr(_api_module, "_orchestrator"),
        getattr(_api_module, "_memory_manager"),
        getattr(_api_module, "_config"),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("")
async def list_memories(
    category: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
) -> dict:
    """列出跨会话记忆"""
    _memory_recall, _, _, _, _ = _get_globals()
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    memories = _memory_recall.get_all(user_id=user_id, category=category, limit=limit)
    return {"memories": memories, "total": len(memories)}


@router.get("/search")
async def search_memories(
    q: str,
    user_id: str | None = None,
    limit: int = 5,
) -> dict:
    """搜索记忆"""
    _memory_recall, _, _, _, _ = _get_globals()
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    if len(q) < 2:
        raise HTTPException(status_code=400, detail="查询关键词至少需要2个字符")
    memories = _memory_recall.recall(query=q, user_id=user_id, limit=limit)
    return {"memories": memories, "query": q, "count": len(memories)}


@router.post("")
async def create_memory(req: CreateMemoryRequest) -> dict:
    """创建跨会话记忆"""
    _memory_recall, _, _, _, _ = _get_globals()
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


@router.get("/{memory_id}")
async def get_memory(memory_id: str) -> dict:
    """获取单条记忆"""
    _memory_recall, _, _, _, _ = _get_globals()
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    memory = _memory_recall.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return memory


@router.patch("/{memory_id}")
async def update_memory(memory_id: str, req: UpdateMemoryRequest) -> dict:
    """更新记忆"""
    _memory_recall, _, _, _, _ = _get_globals()
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    success = _memory_recall.update_memory(memory_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"success": True}


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> dict:
    """删除记忆"""
    _memory_recall, _, _, _, _ = _get_globals()
    if not _memory_recall:
        raise HTTPException(status_code=503, detail="记忆召回未启用")
    if not _memory_recall.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"success": True, "id": memory_id}
