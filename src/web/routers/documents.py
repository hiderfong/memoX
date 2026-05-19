"""Documents and groups router"""
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from auth import AuthUser, require_role

router = APIRouter(prefix="/api", tags=["documents"])


class URLRequest(BaseModel):
    url: str


class DocumentResponse(BaseModel):
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


MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB per file


def _check_url_not_ssrf(url: str) -> None:
    """Raise HTTPException if URL resolves to a private/reserved IP (SSRF protection)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="URL 无法解析主机名")

    try:
        ip_str = socket.gethostbyname(hostname)
    except socket.gaierror as e:
        raise HTTPException(status_code=400, detail=f"DNS 解析失败: {e}") from e

    ip = ipaddress.ip_address(ip_str)
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
        raise HTTPException(status_code=400, detail=f"不允许访问私有/保留 IP 地址: {ip_str}")


# ── Documents ────────────────────────────────────────────────────────────────


@router.get("/documents")
async def list_documents() -> list[DocumentResponse]:
    """列出所有文档"""
    from web.api import _rag_engine
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


@router.post("/documents")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    group_id: str = Form(default="ungrouped"),
) -> DocumentResponse:
    """上传文档"""
    import asyncio
    import contextlib
    import uuid
    from pathlib import Path

    from web.api import _config, _rag_engine

    # 检查 Content-Length
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            cl = int(content_length)
            if cl > MAX_UPLOAD_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件大小 {cl / 1024 / 1024:.1f} MB 超过上限 {MAX_UPLOAD_SIZE / 1024 / 1024:.0f} MB",
                )
        except ValueError:
            pass

    upload_dir = Path(_config.knowledge_base.upload_directory)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / f"{uuid.uuid4().hex}_{file.filename}"
    bytes_written = 0
    try:
        with file_path.open("wb") as dest:
            while chunk := await file.read(65536):
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_SIZE:
                    file_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件大小超过上限 {MAX_UPLOAD_SIZE / 1024 / 1024:.0f} MB",
                    )
                dest.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"文件保存失败: {str(e)}") from e

    try:
        result = await asyncio.wait_for(
            _rag_engine.add_document(file_path, group_id=group_id, original_filename=file.filename),
            timeout=300.0
        )
        doc_info = result.doc_info
        action = result.action
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
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail="文档处理超时，请尝试上传更小的文件或简化文档格式") from e
    except TimeoutError as e:
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=504, detail=str(e)) from e
    except ValueError as e:
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=str(e)) from e
    except Exception as e:
        with contextlib.suppress(Exception):
            file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"文档处理失败: {type(e).__name__}: {str(e)}") from e


@router.post("/documents/url")
async def import_url(request: URLRequest) -> DocumentResponse:
    """从 URL 抓取网页并导入知识库"""
    import asyncio
    import datetime
    import re
    import uuid

    from web.api import WebPageParser, _rag_engine

    url = request.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="URL 必须以 http:// 或 https:// 开头")

    _check_url_not_ssrf(url)

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

    chunks = await parser.chunk(doc_info_raw, chunk_size=500, overlap=50)
    created_at = datetime.datetime.now().isoformat()
    file_size = len(doc_info_raw.content.encode())
    for chunk in chunks:
        chunk.metadata["doc_id"] = doc_id
        chunk.metadata["filename"] = url
        chunk.metadata["type"] = "webpage"
        chunk.metadata["group_id"] = "ungrouped"
        chunk.metadata["created_at"] = created_at
        chunk.metadata["file_size"] = file_size
        chunk.metadata["chunk_count"] = len(chunks)

    if chunks:
        await _rag_engine._index_document_chunks(chunks, "documents")

    from knowledge import DocumentInfo
    doc_info = DocumentInfo(
        id=doc_id,
        filename=url,
        type="webpage",
        chunk_count=len(chunks),
        created_at=created_at,
        size=file_size,
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


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """删除文档（仅管理员）"""
    from web.api import _rag_engine

    success = await _rag_engine.delete_document(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"success": True, "message": "Document deleted"}


# ── Groups ──────────────────────────────────────────────────────────────────


@router.get("/groups")
async def list_groups() -> list[dict]:
    """列出所有分组（含各组文档数）"""
    from web.api import _group_store, _rag_engine

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


@router.post("/groups")
async def create_group(request: GroupCreate) -> dict:
    """新建分组"""
    from web.api import _group_store

    group = _group_store.create_group(request.name, request.color)
    return {"id": group.id, "name": group.name, "color": group.color, "created_at": group.created_at, "doc_count": 0}


@router.put("/groups/{group_id}")
async def update_group(
    group_id: str,
    request: GroupUpdate,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """修改分组名称或颜色（仅管理员）"""
    from web.api import _group_store

    try:
        group = _group_store.update_group(group_id, request.name, request.color)
        return {"id": group.id, "name": group.name, "color": group.color, "created_at": group.created_at}
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Group not found") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/groups/{group_id}")
async def delete_group(
    group_id: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """删除分组，其下文档自动归回未分组（仅管理员）"""
    from knowledge.group_store import UNGROUPED_ID
    from web.api import _group_store, _rag_engine

    try:
        docs = _rag_engine.list_documents()
        for doc in docs:
            if doc.group_id == group_id:
                _rag_engine.move_document_group(doc.id, UNGROUPED_ID)
        _group_store.delete_group(group_id)
        return {"success": True}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/documents/{doc_id}/group")
async def move_document_group(
    doc_id: str,
    request: MoveDocumentGroup,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """修改文档所属分组（仅管理员）"""
    from web.api import _group_store, _rag_engine

    if not _group_store.get_group(request.group_id):
        raise HTTPException(status_code=404, detail="Group not found")
    success = _rag_engine.move_document_group(doc_id, request.group_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"success": True}


@router.get("/documents/{doc_id}/chunks")
async def get_document_chunks(doc_id: str) -> dict:
    """获取文档的所有分块内容"""
    from web.api import _rag_engine

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


@router.get("/documents/search")
async def search_documents(q: str, group_ids: str | None = None) -> dict:
    """全文搜索文档"""
    from web.api import _rag_engine

    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    gids = group_ids.split(",") if group_ids else None
    results = await _rag_engine.search(q, group_ids=gids, top_k=20)
    valid_docs = {d.id: d.filename for d in _rag_engine.list_documents()}
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
