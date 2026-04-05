# Knowledge Base Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为知识库添加分组功能，使用户在智能问答和任务执行时可以选择激活哪些分组。

**Architecture:** 在 ChromaDB chunk metadata 中新增 `group_id` 字段；分组定义持久化到 `data/groups.json`；启动时为历史文档批量补写 `group_id="ungrouped"`；前端在知识库、问答、任务页面分别增加分组管理和选择 UI。

**Tech Stack:** Python/FastAPI (backend), React/Ant Design (frontend), ChromaDB (vector store), pytest (tests)

---

## File Map

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/knowledge/group_store.py` | 新建 | KnowledgeGroup dataclass + GroupStore CRUD |
| `src/knowledge/vector_store.py` | 修改 | 新增 `update_metadata_by_doc_id`、`migrate_add_group_id` |
| `src/knowledge/rag_engine.py` | 修改 | DocumentInfo 增加 group_id、search/add_document/list_documents/move_document_group |
| `src/web/api.py` | 修改 | 分组 CRUD 端点、文档分组端点、修改 chat/tasks 请求体 |
| `frontend/src/App.tsx` | 修改 | DocumentsPage 分组标签+管理抽屉、ChatPage/TasksPage 分组选择器 |
| `tests/test_group_store.py` | 新建 | GroupStore 单元测试 |

---

## Task 1: GroupStore 模块

**Files:**
- Create: `src/knowledge/group_store.py`
- Create: `tests/test_group_store.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_group_store.py`：

```python
import sys, os, tempfile, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from knowledge.group_store import GroupStore, UNGROUPED_ID


def make_store(tmp_path):
    return GroupStore(path=str(tmp_path / "groups.json"))


def test_ungrouped_always_exists(tmp_path):
    store = make_store(tmp_path)
    groups = store.list_groups()
    ids = [g.id for g in groups]
    assert UNGROUPED_ID in ids


def test_create_group(tmp_path):
    store = make_store(tmp_path)
    g = store.create_group("技术文档", "#1890ff")
    assert g.id != UNGROUPED_ID
    assert g.name == "技术文档"
    assert g.color == "#1890ff"
    assert g.id in [x.id for x in store.list_groups()]


def test_create_group_persists(tmp_path):
    store = make_store(tmp_path)
    g = store.create_group("财务", "#52c41a")
    store2 = make_store(tmp_path)
    assert g.id in [x.id for x in store2.list_groups()]


def test_update_group(tmp_path):
    store = make_store(tmp_path)
    g = store.create_group("旧名", "#aaa")
    updated = store.update_group(g.id, name="新名", color="#bbb")
    assert updated.name == "新名"
    assert updated.color == "#bbb"


def test_cannot_rename_ungrouped(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.update_group(UNGROUPED_ID, name="改名")


def test_delete_group(tmp_path):
    store = make_store(tmp_path)
    g = store.create_group("临时", "#ccc")
    store.delete_group(g.id)
    assert g.id not in [x.id for x in store.list_groups()]


def test_cannot_delete_ungrouped(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.delete_group(UNGROUPED_ID)


def test_delete_nonexistent_raises(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(KeyError):
        store.delete_group("nonexistent")


def test_get_group(tmp_path):
    store = make_store(tmp_path)
    g = store.create_group("测试", "#111")
    found = store.get_group(g.id)
    assert found is not None
    assert found.id == g.id


def test_get_nonexistent_returns_none(tmp_path):
    store = make_store(tmp_path)
    assert store.get_group("missing") is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /work/memoX && python3 -m pytest tests/test_group_store.py -v 2>&1 | head -20
```

预期：`ImportError: No module named 'knowledge.group_store'`

- [ ] **Step 3: 创建 `src/knowledge/group_store.py`**

```python
"""知识库分组管理"""

from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
import json
import uuid

UNGROUPED_ID = "ungrouped"


@dataclass
class KnowledgeGroup:
    id: str
    name: str
    color: str
    created_at: str


class GroupStore:
    """分组定义的持久化存储（data/groups.json）"""

    def __init__(self, path: str = "./data/groups.json"):
        self._path = Path(path)
        self._groups: dict[str, KnowledgeGroup] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._groups = {g["id"]: KnowledgeGroup(**g) for g in data}
        if UNGROUPED_ID not in self._groups:
            self._groups[UNGROUPED_ID] = KnowledgeGroup(
                id=UNGROUPED_ID,
                name="未分组",
                color="#999999",
                created_at=datetime.now().isoformat(),
            )
            self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([asdict(g) for g in self._groups.values()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_groups(self) -> list[KnowledgeGroup]:
        return list(self._groups.values())

    def get_group(self, group_id: str) -> KnowledgeGroup | None:
        return self._groups.get(group_id)

    def create_group(self, name: str, color: str = "#1890ff") -> KnowledgeGroup:
        group = KnowledgeGroup(
            id=uuid.uuid4().hex[:8],
            name=name,
            color=color,
            created_at=datetime.now().isoformat(),
        )
        self._groups[group.id] = group
        self._save()
        return group

    def update_group(self, group_id: str, name: str | None = None, color: str | None = None) -> KnowledgeGroup:
        if group_id not in self._groups:
            raise KeyError(f"Group not found: {group_id}")
        if group_id == UNGROUPED_ID and name is not None:
            raise ValueError("Cannot rename the ungrouped group")
        group = self._groups[group_id]
        if name is not None:
            group.name = name
        if color is not None:
            group.color = color
        self._save()
        return group

    def delete_group(self, group_id: str) -> None:
        if group_id == UNGROUPED_ID:
            raise ValueError("Cannot delete the ungrouped group")
        if group_id not in self._groups:
            raise KeyError(f"Group not found: {group_id}")
        del self._groups[group_id]
        self._save()
```

- [ ] **Step 4: 运行测试确认全部通过**

```bash
cd /work/memoX && python3 -m pytest tests/test_group_store.py -v
```

预期：10 tests PASSED

- [ ] **Step 5: Commit**

```bash
cd /work/memoX
git init 2>/dev/null || true
git add src/knowledge/group_store.py tests/test_group_store.py
git commit -m "feat: add GroupStore for knowledge base groups"
```

---

## Task 2: VectorStore — metadata 更新与历史数据迁移

**Files:**
- Modify: `src/knowledge/vector_store.py`（在 `ChromaVectorStore` 类末尾追加两个方法）

- [ ] **Step 1: 在 `ChromaVectorStore` 末尾追加 `update_metadata_by_doc_id`**

在 `get_collection_stats` 方法之后，`_vector_store` 全局变量之前，添加：

```python
    def update_metadata_by_doc_id(
        self,
        doc_id: str,
        metadata_patch: dict,
        collection_name: str = "documents",
    ) -> int:
        """批量更新某文档所有 chunk 的 metadata 字段"""
        collection = self.get_or_create_collection(collection_name)
        results = collection.get(where={"doc_id": doc_id}, include=["metadatas"])
        if not results["ids"]:
            return 0
        new_metadatas = [{**m, **metadata_patch} for m in results["metadatas"]]
        collection.update(ids=results["ids"], metadatas=new_metadatas)
        return len(results["ids"])

    def migrate_add_group_id(self, collection_name: str = "documents") -> int:
        """为所有没有 group_id 的 chunk 补写 group_id='ungrouped'（启动时调用一次）"""
        collection = self.get_or_create_collection(collection_name)
        results = collection.get(include=["metadatas"])
        if not results["ids"]:
            return 0
        ids_to_update: list[str] = []
        updated_metadatas: list[dict] = []
        for chunk_id, meta in zip(results["ids"], results["metadatas"]):
            if not meta.get("group_id"):
                ids_to_update.append(chunk_id)
                updated_metadatas.append({**meta, "group_id": "ungrouped"})
        if ids_to_update:
            collection.update(ids=ids_to_update, metadatas=updated_metadatas)
        return len(ids_to_update)
```

- [ ] **Step 2: 手动验证语法**

```bash
cd /work/memoX && python3 -c "
import sys; sys.path.insert(0, 'src')
from knowledge.vector_store import ChromaVectorStore
print('OK:', hasattr(ChromaVectorStore, 'update_metadata_by_doc_id'))
print('OK:', hasattr(ChromaVectorStore, 'migrate_add_group_id'))
"
```

预期：两行均输出 `OK: True`

- [ ] **Step 3: Commit**

```bash
cd /work/memoX
git add src/knowledge/vector_store.py
git commit -m "feat: add update_metadata_by_doc_id and migrate_add_group_id to VectorStore"
```

---

## Task 3: RAGEngine — 分组支持

**Files:**
- Modify: `src/knowledge/rag_engine.py`

需要修改 4 处：
1. `DocumentInfo` dataclass 新增 `group_id` 字段
2. `add_document()` 新增 `group_id` 参数，写入 chunk metadata
3. `list_documents()` 从 ChromaDB metadata 中读取 `group_id`
4. `search()` 新增 `group_ids` 参数，转为 ChromaDB filter
5. 新增 `move_document_group()` 方法

- [ ] **Step 1: 修改 `DocumentInfo` dataclass（第 14-21 行附近）**

将：
```python
@dataclass
class DocumentInfo:
    """文档信息"""
    id: str
    filename: str
    type: str
    chunk_count: int
    created_at: str
    size: int = 0
```

改为：
```python
@dataclass
class DocumentInfo:
    """文档信息"""
    id: str
    filename: str
    type: str
    chunk_count: int
    created_at: str
    size: int = 0
    group_id: str = "ungrouped"
```

- [ ] **Step 2: 修改 `add_document()` 签名和 chunk metadata 写入**

将方法签名（第 81 行附近）：
```python
    async def add_document(
        self,
        file_path: Path,
        collection_name: str = "documents",
    ) -> DocumentInfo:
```

改为：
```python
    async def add_document(
        self,
        file_path: Path,
        collection_name: str = "documents",
        group_id: str = "ungrouped",
    ) -> DocumentInfo:
```

在 `for chunk in chunks:` 循环中（第 102 行附近），在最后一行 `chunk.metadata["chunk_count"] = len(chunks)` 之后追加：
```python
            chunk.metadata["group_id"] = group_id
```

在 `doc_info = DocumentInfo(...)` 构造处追加 `group_id=group_id`，即：
```python
        doc_info = DocumentInfo(
            id=doc_id,
            filename=document.filename,
            type=document.metadata.get("type", "unknown"),
            chunk_count=len(chunks),
            created_at=created_at,
            size=file_size,
            group_id=group_id,
        )
```

- [ ] **Step 3: 修改 `list_documents()` 以读取 `group_id`**

找到 `list_documents` 中构建 `DocumentInfo` 的地方（第 164 行附近），将：
```python
                result.append(DocumentInfo(
                    id=doc_id,
                    filename=d.get("filename", "unknown"),
                    type=d.get("type", "unknown"),
                    chunk_count=d.get("chunk_count", 0),
                    created_at=d.get("created_at", ""),
                    size=d.get("file_size", 0),
                ))
```

改为：
```python
                result.append(DocumentInfo(
                    id=doc_id,
                    filename=d.get("filename", "unknown"),
                    type=d.get("type", "unknown"),
                    chunk_count=d.get("chunk_count", 0),
                    created_at=d.get("created_at", ""),
                    size=d.get("file_size", 0),
                    group_id=d.get("group_id", "ungrouped"),
                ))
```

- [ ] **Step 4: 修改 `search()` 新增 `group_ids` 参数**

将方法签名（第 176 行附近）：
```python
    async def search(
        self,
        query: str,
        collection_name: str = "documents",
        top_k: int | None = None,
        doc_ids: list[str] | None = None,
    ) -> list[SearchResult]:
```

改为：
```python
    async def search(
        self,
        query: str,
        collection_name: str = "documents",
        top_k: int | None = None,
        doc_ids: list[str] | None = None,
        group_ids: list[str] | None = None,
    ) -> list[SearchResult]:
```

将 filter 构建块（第 186 行附近）：
```python
        # 构建过滤器
        filter_metadata = None
        if doc_ids:
            filter_metadata = {"doc_id": {"$in": doc_ids}}
```

改为：
```python
        # 构建过滤器
        filter_metadata = None
        if doc_ids:
            filter_metadata = {"doc_id": {"$in": doc_ids}}
        elif group_ids is not None:
            filter_metadata = {"group_id": {"$in": group_ids}}
```

- [ ] **Step 5: 新增 `move_document_group()` 方法**

在 `delete_session()` 方法之后、`_rag_engine` 全局变量之前，追加：

```python
    def move_document_group(
        self,
        doc_id: str,
        new_group_id: str,
        collection_name: str = "documents",
    ) -> bool:
        """将文档移到指定分组（更新所有 chunk 的 group_id metadata）"""
        count = self.vector_store.update_metadata_by_doc_id(
            doc_id, {"group_id": new_group_id}, collection_name
        )
        if doc_id in self._documents:
            self._documents[doc_id].group_id = new_group_id
        return count > 0
```

- [ ] **Step 6: 验证语法**

```bash
cd /work/memoX && python3 -c "
import sys; sys.path.insert(0, 'src')
from knowledge.rag_engine import RAGEngine, DocumentInfo
import inspect
sig = inspect.signature(RAGEngine.search)
print('group_ids param:', 'group_ids' in sig.parameters)
sig2 = inspect.signature(RAGEngine.add_document)
print('group_id param:', 'group_id' in sig2.parameters)
print('DocumentInfo group_id field:', hasattr(DocumentInfo, '__dataclass_fields__') and 'group_id' in DocumentInfo.__dataclass_fields__)
print('move_document_group:', hasattr(RAGEngine, 'move_document_group'))
"
```

预期：4 行均输出 `True`

- [ ] **Step 7: Commit**

```bash
cd /work/memoX
git add src/knowledge/rag_engine.py
git commit -m "feat: add group_id support to RAGEngine (DocumentInfo, search, add_document, move_document_group)"
```

---

## Task 4: API — 分组 CRUD 端点

**Files:**
- Modify: `src/web/api.py`

- [ ] **Step 1: 在 import 区（第 1-40 行）新增分组相关导入**

在 `from config import load_config, Config` 之后，追加：

```python
from dataclasses import asdict
from knowledge.group_store import GroupStore, UNGROUPED_ID
```

- [ ] **Step 2: 新增全局变量 `_group_store`**

在第 61-63 行（`_config`, `_rag_engine`, `_task_planner` 声明处），追加：

```python
_group_store: GroupStore | None = None
```

- [ ] **Step 3: 在 `startup()` 函数末尾，`print("✅ MemoX 启动完成")` 之前，初始化分组存储并执行迁移**

```python
    # 初始化分组存储
    global _group_store
    _group_store = GroupStore(path=str(Path(_config.knowledge_base.persist_directory).parent / "groups.json"))

    # 历史文档迁移：为无 group_id 的 chunk 补写 "ungrouped"
    migrated = _rag_engine.vector_store.migrate_add_group_id()
    if migrated > 0:
        print(f"   - 迁移 {migrated} 个历史 chunk，补写 group_id=ungrouped")
```

- [ ] **Step 4: 新增请求体模型**

在 `DocumentResponse` 类定义之后，追加：

```python
class GroupCreate(BaseModel):
    name: str
    color: str = "#1890ff"


class GroupUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class MoveDocumentGroup(BaseModel):
    group_id: str
```

- [ ] **Step 5: 修改 `DocumentResponse` 新增 `group_id`**

将：
```python
class DocumentResponse(BaseModel):
    """文档响应"""
    id: str
    filename: str
    type: str
    chunk_count: int
    created_at: str
    size: int
```

改为：
```python
class DocumentResponse(BaseModel):
    """文档响应"""
    id: str
    filename: str
    type: str
    chunk_count: int
    created_at: str
    size: int
    group_id: str = "ungrouped"
```

- [ ] **Step 6: 修改 `list_documents` 端点以返回 `group_id`**

将 `list_documents` 端点中的 `DocumentResponse(...)` 构造改为：

```python
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
```

- [ ] **Step 7: 新增分组 CRUD 端点**

在 `# ==================== RAG 问答 API ====================` 注释之前，插入：

```python
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
```

- [ ] **Step 8: 验证语法**

```bash
cd /work/memoX && PYTHONPATH=src python3 -c "from web.api import app; print('API syntax OK')"
```

预期：`API syntax OK`

- [ ] **Step 9: Commit**

```bash
cd /work/memoX
git add src/web/api.py
git commit -m "feat: add group CRUD API endpoints and document group move endpoint"
```

---

## Task 5: API — 修改上传、聊天、任务端点

**Files:**
- Modify: `src/web/api.py`

- [ ] **Step 1: 修改 `upload_document` 支持 `group_id` 表单字段**

在 import 区顶部确保有 `Form` 导入（FastAPI 的 `Form`）：

```python
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect, Request
```

将 `upload_document` 函数签名：
```python
async def upload_document(file: UploadFile = File(...)) -> DocumentResponse:
```

改为：
```python
async def upload_document(
    file: UploadFile = File(...),
    group_id: str = Form(default="ungrouped"),
) -> DocumentResponse:
```

将函数内 `_rag_engine.add_document(file_path)` 调用：
```python
        doc_info = await asyncio.wait_for(
            _rag_engine.add_document(file_path),
            timeout=300.0
        )
```

改为：
```python
        doc_info = await asyncio.wait_for(
            _rag_engine.add_document(file_path, group_id=group_id),
            timeout=300.0
        )
```

将返回值加上 `group_id`：
```python
        return DocumentResponse(
            id=doc_info.id,
            filename=doc_info.filename,
            type=doc_info.type,
            chunk_count=doc_info.chunk_count,
            created_at=doc_info.created_at,
            size=doc_info.size,
            group_id=doc_info.group_id,
        )
```

- [ ] **Step 2: 修改 `ChatRequest` 新增 `active_group_ids`**

将：
```python
class ChatRequest(BaseModel):
    """聊天请求"""
    message: str
    session_id: str | None = None
    use_rag: bool = True
    stream: bool = True
```

改为：
```python
class ChatRequest(BaseModel):
    """聊天请求"""
    message: str
    session_id: str | None = None
    use_rag: bool = True
    stream: bool = True
    active_group_ids: list[str] | None = None
```

- [ ] **Step 3: 修改 `TaskRequest` 新增 `active_group_ids`**

将：
```python
class TaskRequest(BaseModel):
    """任务请求"""
    description: str
    context: dict | None = None
    generate_suggestions: bool = True
```

改为：
```python
class TaskRequest(BaseModel):
    """任务请求"""
    description: str
    context: dict | None = None
    generate_suggestions: bool = True
    active_group_ids: list[str] | None = None
```

- [ ] **Step 4: 修改 `chat` 端点（非流式）传递 `group_ids`**

找到非流式 `chat` 端点中的搜索调用：
```python
        search_results = await _rag_engine.search(request.message)
```

改为：
```python
        search_results = await _rag_engine.search(request.message, group_ids=request.active_group_ids)
```

- [ ] **Step 5: 修改 `chat_stream` 端点传递 `group_ids`**

在流式 `generate()` 函数中找到：
```python
            search_results = await _rag_engine.search(user_message)
```

等等，这是 WebSocket 里的，而 `chat_stream` 里是：
```python
            search_results = await _rag_engine.search(request.message)
```

将其改为：
```python
            search_results = await _rag_engine.search(request.message, group_ids=request.active_group_ids)
```

- [ ] **Step 6: 修复 URL 导入端点，补写 `group_id`**

找到 `import_url` 端点中 `for chunk in chunks:` 循环，在循环体末尾追加：

```python
        chunk.metadata["group_id"] = "ungrouped"
```

- [ ] **Step 7: 验证语法**

```bash
cd /work/memoX && PYTHONPATH=src python3 -c "from web.api import app, ChatRequest, TaskRequest; import inspect; sig = inspect.signature(ChatRequest); print('ChatRequest active_group_ids:', 'active_group_ids' in ChatRequest.model_fields); print('TaskRequest active_group_ids:', 'active_group_ids' in TaskRequest.model_fields)"
```

预期：两行均输出 `True`

- [ ] **Step 8: Commit**

```bash
cd /work/memoX
git add src/web/api.py
git commit -m "feat: pass active_group_ids to RAG search in chat and task endpoints"
```

---

## Task 6: 前端 — 分组类型、API 方法、DocumentsPage 分组标签

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 在 `AuthUser` 接口之前新增 `KnowledgeGroup` 接口**

在 `// ==================== 认证状态 ====================` 注释之前，插入：

```typescript
// ==================== 分组类型 ====================

interface KnowledgeGroup {
  id: string;
  name: string;
  color: string;
  created_at: string;
  doc_count: number;
}
```

- [ ] **Step 2: 在 `api` 对象中新增分组 API 方法**

在 `api` 对象（`const api = {`）的最后一个属性之后、`};` 之前，追加：

```typescript
  // 分组
  listGroups: () => axios.get(`${API_BASE}/groups`),
  createGroup: (name: string, color: string) =>
    axios.post(`${API_BASE}/groups`, { name, color }),
  updateGroup: (id: string, data: { name?: string; color?: string }) =>
    axios.put(`${API_BASE}/groups/${id}`, data),
  deleteGroup: (id: string) => axios.delete(`${API_BASE}/groups/${id}`),
  moveDocumentGroup: (docId: string, groupId: string) =>
    axios.put(`${API_BASE}/documents/${docId}/group`, { group_id: groupId }),
```

- [ ] **Step 3: 修改 `DocumentsPage` 顶部 state，增加分组相关状态**

找到 `DocumentsPage` 组件中现有的 state 声明（第 252 行附近）：
```typescript
  const [documents, setDocuments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
```

替换为：
```typescript
  const [documents, setDocuments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [groups, setGroups] = useState<KnowledgeGroup[]>([]);
  const [activeGroupFilter, setActiveGroupFilter] = useState<string>('all');
  const [groupDrawerOpen, setGroupDrawerOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [newGroupColor, setNewGroupColor] = useState('#1890ff');
  const [editingGroup, setEditingGroup] = useState<KnowledgeGroup | null>(null);
  const [editGroupName, setEditGroupName] = useState('');
```

- [ ] **Step 4: 新增 `fetchGroups` 函数并在 `useEffect` 中调用**

在 `fetchDocuments` 函数之后，`useEffect` 之前，插入：

```typescript
  const fetchGroups = async () => {
    try {
      const res = await api.listGroups();
      setGroups(res.data);
    } catch (err) {
      console.error('获取分组失败', err);
    }
  };
```

将 `useEffect` 改为：
```typescript
  useEffect(() => {
    fetchDocuments();
    fetchGroups();
  }, []);
```

- [ ] **Step 5: 新增 `handleMoveGroup` 函数**

在 `handleDelete` 函数之后，追加：

```typescript
  const handleMoveGroup = async (docId: string, groupId: string) => {
    try {
      await api.moveDocumentGroup(docId, groupId);
      message.success('已移动到新分组');
      await fetchDocuments();
      await fetchGroups();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '移动失败');
    }
  };
```

- [ ] **Step 6: 修改 `DocumentsPage` 的 return，增加分组过滤标签栏**

在 `return (` 之后的 `<div>` 内，`<Card title="知识库管理"` 之前，插入：

```tsx
      {/* 分组标签栏 */}
      <Card style={{ marginBottom: 16 }} bodyStyle={{ padding: '12px 16px' }}>
        <Space wrap>
          <Tag
            color={activeGroupFilter === 'all' ? '#1890ff' : 'default'}
            style={{ cursor: 'pointer', fontSize: 13 }}
            onClick={() => setActiveGroupFilter('all')}
          >
            全部 ({documents.length})
          </Tag>
          {groups.map(g => (
            <Tag
              key={g.id}
              color={activeGroupFilter === g.id ? g.color : 'default'}
              style={{ cursor: 'pointer', fontSize: 13 }}
              onClick={() => setActiveGroupFilter(g.id)}
            >
              {g.name} ({g.doc_count})
            </Tag>
          ))}
          <Button
            size="small"
            icon={<SettingOutlined />}
            onClick={() => setGroupDrawerOpen(true)}
          >
            管理分组
          </Button>
        </Space>
      </Card>
```

- [ ] **Step 7: 修改文档列表以显示分组标签和移动操作**

在 `"已上传文档"` 的 `List` 中，将 `renderItem` 里文档 actions 的 `delete` 按钮数组，改为包含"移动到分组"的选择：

找到：
```tsx
              actions={[
                  <Button 
                    key="delete" 
                    type="text" 
                    danger 
                    icon={<DeleteOutlined />}
                    onClick={() => handleDelete(doc.id)}
                  >
                    删除
                  </Button>
                ]}
```

改为：
```tsx
              actions={[
                  <select
                    key="move"
                    value={doc.group_id || 'ungrouped'}
                    onChange={e => handleMoveGroup(doc.id, e.target.value)}
                    style={{ fontSize: 12, padding: '2px 4px', borderRadius: 4, border: '1px solid #d9d9d9', cursor: 'pointer' }}
                  >
                    {groups.map(g => (
                      <option key={g.id} value={g.id}>{g.name}</option>
                    ))}
                  </select>,
                  <Button 
                    key="delete" 
                    type="text" 
                    danger 
                    icon={<DeleteOutlined />}
                    onClick={() => handleDelete(doc.id)}
                  >
                    删除
                  </Button>
                ]}
```

在文档 meta `description` 中的 `<Space>` 内，在第一个 `<Tag>{doc.type}</Tag>` 之前，插入分组标签：

```tsx
                      {groups.find(g => g.id === (doc.group_id || 'ungrouped')) && (
                        <Tag color={groups.find(g => g.id === (doc.group_id || 'ungrouped'))?.color}>
                          {groups.find(g => g.id === (doc.group_id || 'ungrouped'))?.name}
                        </Tag>
                      )}
```

- [ ] **Step 8: 修改文档列表的 dataSource 加入分组过滤**

找到 `<List dataSource={documents}` 将 dataSource 改为：

```tsx
            dataSource={activeGroupFilter === 'all' ? documents : documents.filter(d => (d.group_id || 'ungrouped') === activeGroupFilter)}
```

- [ ] **Step 9: Commit（前端分组标签部分）**

```bash
cd /work/memoX
git add frontend/src/App.tsx
git commit -m "feat: add group filter tabs and move-to-group action in DocumentsPage"
```

---

## Task 7: 前端 — 分组管理抽屉 + Chat/Tasks 分组选择器

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 在 `DocumentsPage` 的 return 末尾（`</div>` 之前）插入分组管理 Drawer**

```tsx
      {/* 分组管理抽屉 */}
      <Drawer
        title="管理分组"
        placement="right"
        open={groupDrawerOpen}
        onClose={() => { setGroupDrawerOpen(false); setEditingGroup(null); setNewGroupName(''); }}
        width={360}
      >
        <div style={{ marginBottom: 16 }}>
          <Input
            placeholder="新分组名称"
            value={newGroupName}
            onChange={e => setNewGroupName(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <Space>
            <input
              type="color"
              value={newGroupColor}
              onChange={e => setNewGroupColor(e.target.value)}
              style={{ width: 40, height: 32, cursor: 'pointer', border: 'none' }}
            />
            <Button
              type="primary"
              disabled={!newGroupName.trim()}
              onClick={async () => {
                try {
                  await api.createGroup(newGroupName.trim(), newGroupColor);
                  message.success('分组已创建');
                  setNewGroupName('');
                  await fetchGroups();
                } catch (err: any) {
                  message.error(err.response?.data?.detail || '创建失败');
                }
              }}
            >
              创建分组
            </Button>
          </Space>
        </div>
        <Divider />
        <List
          dataSource={groups}
          renderItem={g => (
            <List.Item
              actions={g.id === 'ungrouped' ? [] : [
                <Button
                  key="del"
                  type="text"
                  danger
                  size="small"
                  icon={<DeleteOutlined />}
                  onClick={async () => {
                    try {
                      await api.deleteGroup(g.id);
                      message.success('分组已删除，文档已归回未分组');
                      await fetchGroups();
                      await fetchDocuments();
                    } catch (err: any) {
                      message.error(err.response?.data?.detail || '删除失败');
                    }
                  }}
                />,
              ]}
            >
              {editingGroup?.id === g.id ? (
                <Space>
                  <Input
                    size="small"
                    value={editGroupName}
                    onChange={e => setEditGroupName(e.target.value)}
                    style={{ width: 120 }}
                  />
                  <Button
                    size="small"
                    type="primary"
                    onClick={async () => {
                      try {
                        await api.updateGroup(g.id, { name: editGroupName });
                        setEditingGroup(null);
                        await fetchGroups();
                      } catch (err: any) {
                        message.error(err.response?.data?.detail || '更新失败');
                      }
                    }}
                  >
                    保存
                  </Button>
                  <Button size="small" onClick={() => setEditingGroup(null)}>取消</Button>
                </Space>
              ) : (
                <Space
                  style={{ cursor: g.id !== 'ungrouped' ? 'pointer' : 'default' }}
                  onClick={() => {
                    if (g.id !== 'ungrouped') {
                      setEditingGroup(g);
                      setEditGroupName(g.name);
                    }
                  }}
                >
                  <Tag color={g.color}>{g.name}</Tag>
                  <Text type="secondary">{g.doc_count} 篇文档</Text>
                  {g.id !== 'ungrouped' && <Text type="secondary" style={{ fontSize: 11 }}>（点击重命名）</Text>}
                </Space>
              )}
            </List.Item>
          )}
        />
      </Drawer>
```

- [ ] **Step 2: 修改 `ChatPage` — 新增分组选择器状态**

找到 `ChatPage` 组件的 state 声明区，在现有 state 之后追加：

```typescript
  const [groups, setGroups] = useState<KnowledgeGroup[]>([]);
  const [activeGroupIds, setActiveGroupIds] = useState<string[]>([]);
```

在 `useEffect` 中追加分组加载：

```typescript
  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    api.listGroups().then(res => {
      setGroups(res.data);
      setActiveGroupIds(res.data.map((g: KnowledgeGroup) => g.id));
    }).catch(() => {});
  }, []);
```

- [ ] **Step 3: 修改 `ChatPage` 的 `handleSend` 以传递 `active_group_ids`**

找到 chat 请求调用：
```typescript
      const res = await api.chat(input, sessionId || undefined);
```

改为：
```typescript
      const allGroupIds = groups.map(g => g.id);
      const isAllSelected = activeGroupIds.length === allGroupIds.length;
      const res = await api.chat(input, sessionId || undefined, true, isAllSelected ? null : activeGroupIds);
```

同时修改 `api.chat` 的调用签名，更新 `api` 对象中的 `chat` 方法，支持第 4 个参数：

```typescript
  chat: (message: string, sessionId?: string, useRag: boolean = true, activeGroupIds?: string[] | null) =>
    axios.post(`${API_BASE}/chat`, { message, session_id: sessionId, use_rag: useRag, stream: false, active_group_ids: activeGroupIds }),
```

- [ ] **Step 4: 在 `ChatPage` 输入框上方插入分组选择器**

找到 `<div style={{ borderTop: '1px solid #f0f0f0', paddingTop: 16 }}>` 之前，插入：

```tsx
      {groups.length > 1 && (
        <div style={{ borderTop: '1px solid #f0f0f0', paddingTop: 12, marginBottom: 4 }}>
          <Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>激活分组：</Text>
          <Checkbox.Group
            value={activeGroupIds}
            onChange={vals => setActiveGroupIds(vals as string[])}
            options={groups.map(g => ({ label: <Tag color={g.color}>{g.name}</Tag>, value: g.id }))}
          />
        </div>
      )}
```

在 App.tsx 顶部 import 中确保引入 `Checkbox`（Ant Design）：

```typescript
import { Layout, Menu, Typography, Card, Button, Upload, List, Space, Avatar, Input, message, Spin, Tag, Progress, Badge, Drawer, Timeline, Alert, Empty, Tooltip, Form, Divider, Checkbox } from 'antd';
```

- [ ] **Step 5: 修改 `TasksPage` — 新增分组选择器**

在 `TasksPage` 的 state 声明区追加：

```typescript
  const [groups, setGroups] = useState<KnowledgeGroup[]>([]);
  const [activeGroupIds, setActiveGroupIds] = useState<string[]>([]);
```

在 `useEffect` 中追加：

```typescript
  useEffect(() => {
    fetchTasks();
    api.listGroups().then(res => {
      setGroups(res.data);
      setActiveGroupIds(res.data.map((g: KnowledgeGroup) => g.id));
    }).catch(() => {});
  }, []);
```

在 `handleExecute` 中，将 `api.createTask(taskInput)` 改为：

```typescript
      const allGroupIds = groups.map(g => g.id);
      const isAllSelected = activeGroupIds.length === allGroupIds.length;
      const res = await api.createTask(taskInput, undefined, isAllSelected ? null : activeGroupIds);
```

更新 `api.createTask`：

```typescript
  createTask: (description: string, context?: object, activeGroupIds?: string[] | null) =>
    axios.post(`${API_BASE}/tasks`, { description, context, generate_suggestions: true, active_group_ids: activeGroupIds }),
```

在 `<TextArea>` 之前（任务输入框上方），插入分组选择器：

```tsx
        {groups.length > 1 && (
          <div style={{ marginBottom: 12 }}>
            <Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>激活知识库分组：</Text>
            <Checkbox.Group
              value={activeGroupIds}
              onChange={vals => setActiveGroupIds(vals as string[])}
              options={groups.map(g => ({ label: <Tag color={g.color}>{g.name}</Tag>, value: g.id }))}
            />
          </div>
        )}
```

- [ ] **Step 6: Commit**

```bash
cd /work/memoX
git add frontend/src/App.tsx
git commit -m "feat: add group management drawer, group selectors in Chat and Tasks pages"
```

---

## Task 8: 构建前端并冒烟测试

**Files:**
- Build: `frontend/dist/`

- [ ] **Step 1: 重启后端服务**

```bash
pkill -f "uvicorn" 2>/dev/null; sleep 1
cd /work/memoX && PYTHONPATH=src nohup uvicorn src.web.api:app --host 0.0.0.0 --port 8080 --reload > /tmp/backend.log 2>&1 &
sleep 3 && curl -s http://localhost:8080/api/health
```

预期：`{"status":"healthy",...}`

- [ ] **Step 2: 验证新增 API 端点**

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"${MEMOX_ADMIN_PASSWORD}"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

echo "=== 分组列表 ===" && curl -s http://localhost:8080/api/groups -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

echo "=== 新建分组 ===" && curl -s -X POST http://localhost:8080/api/groups \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"测试分组","color":"#52c41a"}' | python3 -m json.tool
```

预期：分组列表包含 `"未分组"`，新建分组返回包含新 id 的 JSON。

- [ ] **Step 3: 验证文档 API 返回 `group_id`**

```bash
curl -s http://localhost:8080/api/documents -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
docs = json.load(sys.stdin)
print(f'文档数: {len(docs)}')
for d in docs[:2]:
    print(f'  {d[\"filename\"]}: group_id={d.get(\"group_id\", \"MISSING\")}')
"
```

预期：每个文档显示 `group_id=ungrouped`

- [ ] **Step 4: 构建前端**

```bash
cd /work/memoX/frontend && npm run build 2>&1 | tail -20
```

预期：`✓ built in X.XXs` 且无 TypeScript 错误

- [ ] **Step 5: 重启前端 preview**

```bash
pkill -f "vite preview" 2>/dev/null; sleep 1
cd /work/memoX/frontend && nohup npm run preview -- --host 0.0.0.0 --port 3000 > /tmp/frontend.log 2>&1 &
sleep 2 && curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
```

预期：`200`

- [ ] **Step 6: 清理测试数据并 final commit**

```bash
# 删除测试分组（获取其 ID 后删除）
TEST_GID=$(curl -s http://localhost:8080/api/groups -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json
groups = json.load(sys.stdin)
for g in groups:
    if g['name'] == '测试分组':
        print(g['id'])
")
[ -n "$TEST_GID" ] && curl -s -X DELETE http://localhost:8080/api/groups/$TEST_GID -H "Authorization: Bearer $TOKEN"

cd /work/memoX
git add -A
git commit -m "feat: knowledge base groups - complete implementation"
```

---

## 验收标准

- [ ] 知识库页面有分组标签栏，可按分组过滤文档
- [ ] 每个文档显示所属分组的彩色标签
- [ ] 文档可通过下拉菜单移动到不同分组
- [ ] "管理分组"抽屉可新建、重命名、删除分组
- [ ] 删除分组后，文档自动显示在"未分组"
- [ ] 智能问答页面有分组多选器，默认全选
- [ ] 任务执行页面有分组多选器，默认全选
- [ ] 历史文档（7 篇）在启动时自动迁移到"未分组"
