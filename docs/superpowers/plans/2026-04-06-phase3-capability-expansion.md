# Phase 3 — Capability Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add document preview with full-text search, image OCR (Qwen VL + pytesseract fallback), and human-in-the-loop task intervention to MemoX.

**Architecture:** Three independent features built sequentially. Item 7 adds chunk retrieval API + frontend Drawer/search. Item 9 adds ImageParser with dual OCR backends. Item 8 adds WebSocket-driven feedback loop in the iterative orchestrator.

**Tech Stack:** Python/FastAPI, ChromaDB, React/Ant Design, DashScope OpenAI-compatible API (qwen-vl-plus), pytesseract (fallback)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/knowledge/vector_store.py` | Modify | Add `get_chunks_by_doc()` |
| `src/knowledge/rag_engine.py` | Modify | Add `get_document_chunks()` wrapper |
| `src/web/api.py` | Modify | Add chunk/search/feedback endpoints, WS broadcast, ImageParser config |
| `src/knowledge/document_parser.py` | Modify | Add `ImageParser` class, register in `DocumentParser` |
| `src/coordinator/iterative_orchestrator.py` | Modify | Add feedback wait logic + broadcast callback |
| `frontend/src/App.tsx` | Modify | Document Drawer, search bar, feedback Modal |
| `tests/test_document_chunks.py` | Create | Tests for chunk retrieval and search |
| `tests/test_image_parser.py` | Create | Tests for ImageParser OCR |

---

### Task 1: Backend — get_chunks_by_doc in vector_store

**Files:**
- Modify: `src/knowledge/vector_store.py`
- Create: `tests/test_document_chunks.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_document_chunks.py`:

```python
"""文档 chunk 检索测试"""
import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from knowledge.document_parser import TextChunk


@pytest.fixture
def vector_store(tmp_path):
    from knowledge.vector_store import ChromaVectorStore, SentenceTransformerEmbedding
    embedding = SentenceTransformerEmbedding()
    store = ChromaVectorStore(persist_directory=str(tmp_path / "chroma"), embedding_function=embedding)
    return store


@pytest.mark.asyncio
async def test_get_chunks_by_doc(vector_store):
    """按 doc_id 检索所有 chunk"""
    chunks = [
        TextChunk(id="d1_chunk_0", content="第一段内容", metadata={"doc_id": "d1", "filename": "test.md", "chunk_index": 0}),
        TextChunk(id="d1_chunk_1", content="第二段内容", metadata={"doc_id": "d1", "filename": "test.md", "chunk_index": 1}),
        TextChunk(id="d2_chunk_0", content="另一个文档", metadata={"doc_id": "d2", "filename": "other.md", "chunk_index": 0}),
    ]
    await vector_store.add_chunks(chunks)

    result = vector_store.get_chunks_by_doc("d1")
    assert len(result) == 2
    assert result[0]["chunk_index"] == 0
    assert result[1]["chunk_index"] == 1
    assert "第一段内容" in result[0]["content"]


@pytest.mark.asyncio
async def test_get_chunks_by_doc_not_found(vector_store):
    """不存在的 doc_id 返回空列表"""
    result = vector_store.get_chunks_by_doc("nonexistent")
    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_document_chunks.py -v`
Expected: FAIL with AttributeError: 'ChromaVectorStore' object has no attribute 'get_chunks_by_doc'

- [ ] **Step 3: Implement get_chunks_by_doc**

In `src/knowledge/vector_store.py`, add this method to `ChromaVectorStore` after `list_documents` (around line 376):

```python
    def get_chunks_by_doc(self, doc_id: str, collection_name: str = "documents") -> list[dict]:
        """获取某文档的所有 chunk，按 chunk_index 排序"""
        collection = self.get_or_create_collection(collection_name)
        results = collection.get(
            where={"doc_id": doc_id},
            include=["documents", "metadatas"],
        )
        if not results["ids"]:
            return []
        chunks = []
        for i, chunk_id in enumerate(results["ids"]):
            meta = results["metadatas"][i] if results["metadatas"] else {}
            chunks.append({
                "id": chunk_id,
                "content": results["documents"][i] if results["documents"] else "",
                "chunk_index": meta.get("chunk_index", i),
                "metadata": meta,
            })
        chunks.sort(key=lambda c: c["chunk_index"])
        return chunks
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_document_chunks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/knowledge/vector_store.py tests/test_document_chunks.py
git commit -m "feat: add get_chunks_by_doc to ChromaVectorStore"
```

---

### Task 2: Backend — Document chunk + search API endpoints

**Files:**
- Modify: `src/knowledge/rag_engine.py`
- Modify: `src/web/api.py`

- [ ] **Step 1: Add get_document_chunks to RAGEngine**

In `src/knowledge/rag_engine.py`, add after the `delete_document` method (around line 149):

```python
    def get_document_chunks(self, doc_id: str, collection_name: str = "documents") -> list[dict]:
        """获取文档的所有分块内容"""
        return self.vector_store.get_chunks_by_doc(doc_id, collection_name)
```

- [ ] **Step 2: Add GET /api/documents/{doc_id}/chunks endpoint**

In `src/web/api.py`, add after the `move_document_group` endpoint (search for `async def move_document_group`):

```python
@app.get("/api/documents/{doc_id}/chunks")
async def get_document_chunks(doc_id: str) -> dict:
    """获取文档的所有分块内容"""
    chunks = _rag_engine.get_document_chunks(doc_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="Document not found or has no chunks")
    # 从第一个 chunk 的 metadata 获取 filename
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
```

- [ ] **Step 3: Add GET /api/documents/search endpoint**

Add right after the chunks endpoint:

```python
@app.get("/api/documents/search")
async def search_documents(q: str, group_ids: str | None = None) -> dict:
    """全文搜索文档"""
    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    gids = group_ids.split(",") if group_ids else None
    results = await _rag_engine.search(q, group_ids=gids, top_k=20)
    return {
        "query": q,
        "results": [
            {
                "doc_id": r.metadata.get("doc_id", ""),
                "filename": r.metadata.get("filename", "unknown"),
                "content": r.content,
                "score": r.score,
                "chunk_index": r.metadata.get("chunk_index", 0),
                "group_id": r.metadata.get("group_id", "ungrouped"),
            }
            for r in results
        ],
    }
```

- [ ] **Step 4: Verify syntax**

Run: `python -c "import ast; ast.parse(open('src/web/api.py').read()); print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add src/knowledge/rag_engine.py src/web/api.py
git commit -m "feat: add document chunks + search API endpoints"
```

---

### Task 3: Frontend — Document detail Drawer + search bar

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add API methods**

In the `api` object, add:

```typescript
  // 文档 chunks + 搜索
  getDocumentChunks: (docId: string) => axios.get(`${API_BASE}/documents/${docId}/chunks`),
  searchDocuments: (q: string, groupIds?: string) =>
    axios.get(`${API_BASE}/documents/search`, { params: { q, group_ids: groupIds } }),
```

- [ ] **Step 2: Add document detail Drawer to DocumentsPage**

In the `DocumentsPage` component, add state:

```typescript
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerDoc, setDrawerDoc] = useState<any>(null);
  const [drawerChunks, setDrawerChunks] = useState<any[]>([]);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<any[] | null>(null);
  const [searching, setSearching] = useState(false);
```

Add handlers:

```typescript
  const handleViewChunks = async (doc: any) => {
    setDrawerDoc(doc);
    setDrawerOpen(true);
    setChunksLoading(true);
    try {
      const res = await api.getDocumentChunks(doc.id);
      setDrawerChunks(res.data.chunks || []);
    } catch (err) {
      message.error('获取文档内容失败');
      setDrawerChunks([]);
    } finally {
      setChunksLoading(false);
    }
  };

  const handleSearch = async (value: string) => {
    if (!value.trim()) { setSearchResults(null); return; }
    setSearching(true);
    try {
      const res = await api.searchDocuments(value.trim());
      setSearchResults(res.data.results || []);
    } catch (err) {
      message.error('搜索失败');
    } finally {
      setSearching(false);
    }
  };
```

- [ ] **Step 3: Add search bar above document list**

In the DocumentsPage return JSX, add a search bar above the existing group tabs Card:

```tsx
      <Card style={{ marginBottom: 16 }} bodyStyle={{ padding: '12px 16px' }}>
        <Input.Search
          placeholder="搜索文档内容..."
          allowClear
          enterButton="搜索"
          loading={searching}
          onSearch={handleSearch}
          onChange={e => { if (!e.target.value) setSearchResults(null); }}
          style={{ maxWidth: 500 }}
        />
      </Card>
```

When `searchResults` is not null, show search results instead of the document list:

```tsx
      {searchResults !== null ? (
        <Card title={<Space>搜索结果 ({searchResults.length}) <Button size="small" onClick={() => setSearchResults(null)}>返回文档列表</Button></Space>}>
          <List
            dataSource={searchResults}
            locale={{ emptyText: '无匹配结果' }}
            renderItem={(r: any) => (
              <List.Item>
                <List.Item.Meta
                  avatar={<Avatar icon={<FileSearchOutlined />} style={{ background: '#1890ff' }} />}
                  title={<Space><Text>{r.filename}</Text><Tag color="green">{Math.round(r.score * 100)}%</Tag></Space>}
                  description={<Text type="secondary" style={{ fontSize: 12 }}>{r.content.slice(0, 200)}...</Text>}
                />
              </List.Item>
            )}
          />
        </Card>
      ) : (
        /* existing document list Card goes here */
      )}
```

Note: `Input` from antd and `FileSearchOutlined` are already imported at the top of the file.

- [ ] **Step 4: Add the Drawer component**

Add at the end of DocumentsPage's return (before the closing `</div>`):

```tsx
      <Drawer
        title={drawerDoc?.filename || '文档详情'}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={600}
      >
        {drawerDoc && (
          <div style={{ marginBottom: 16 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Text><strong>类型:</strong> {drawerDoc.type}</Text>
              <Text><strong>大小:</strong> {formatSize(drawerDoc.size)}</Text>
              <Text><strong>分块数:</strong> {drawerDoc.chunk_count}</Text>
              <Text><strong>创建时间:</strong> {drawerDoc.created_at}</Text>
            </Space>
            <Divider />
          </div>
        )}
        {chunksLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
        ) : (
          <List
            dataSource={drawerChunks}
            renderItem={(chunk: any) => (
              <List.Item>
                <Card size="small" title={<Tag>#{chunk.index}</Tag>} style={{ width: '100%' }}>
                  <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0, maxHeight: 200, overflow: 'auto' }}>
                    {chunk.content}
                  </pre>
                </Card>
              </List.Item>
            )}
          />
        )}
      </Drawer>
```

Note: `Drawer`, `Divider`, `Spin` are already imported from antd.

- [ ] **Step 5: Make document filename clickable**

In the document list's `renderItem` (the existing `List` that shows documents), find where the document filename is displayed and wrap it in a clickable link. The document list renders items — find the `List.Item.Meta` `title` and change it to:

```tsx
title={<a onClick={() => handleViewChunks(item)}>{item.filename}</a>}
```

- [ ] **Step 6: Build and verify**

Run: `cd /work/memoX/frontend && npm run build`

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: add document preview Drawer + full-text search"
```

---

### Task 4: Backend — ImageParser with Qwen VL + pytesseract fallback

**Files:**
- Modify: `src/knowledge/document_parser.py`
- Create: `tests/test_image_parser.py`

- [ ] **Step 1: Write the test**

Create `tests/test_image_parser.py`:

```python
"""图片 OCR 解析器测试"""
import sys, os, pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from knowledge.document_parser import ImageParser, DocumentParser


def _create_test_image(path: Path) -> Path:
    """创建一个简单的测试 PNG 图片"""
    # 1x1 白色 PNG (最小合法 PNG)
    import base64
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
    )
    path.write_bytes(png_data)
    return path


@pytest.mark.asyncio
async def test_image_parser_qwen_vl_success():
    """Qwen VL 主路径成功返回 OCR 文本"""
    parser = ImageParser(dashscope_api_key="fake-key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "图片中的文字：Hello World"}}]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            _create_test_image(Path(f.name))
            doc = await parser.parse(Path(f.name), "img_001")
            os.unlink(f.name)

    assert "Hello World" in doc.content
    assert doc.metadata["type"] == "image"
    assert doc.metadata["ocr_method"] == "qwen-vl"


@pytest.mark.asyncio
async def test_image_parser_fallback_to_pytesseract():
    """Qwen VL 失败时回退到 pytesseract"""
    parser = ImageParser(dashscope_api_key="fake-key")

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Network error")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        with patch.object(parser, "_ocr_pytesseract", return_value="Fallback text"):
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                _create_test_image(Path(f.name))
                doc = await parser.parse(Path(f.name), "img_002")
                os.unlink(f.name)

    assert "Fallback text" in doc.content
    assert doc.metadata["ocr_method"] == "pytesseract"


@pytest.mark.asyncio
async def test_image_parser_no_api_key_uses_pytesseract():
    """无 API key 时直接用 pytesseract"""
    parser = ImageParser(dashscope_api_key="")

    with patch.object(parser, "_ocr_pytesseract", return_value="Local OCR result"):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            _create_test_image(Path(f.name))
            doc = await parser.parse(Path(f.name), "img_003")
            os.unlink(f.name)

    assert "Local OCR result" in doc.content


def test_document_parser_registers_image_types():
    """DocumentParser 注册了图片格式"""
    dp = DocumentParser()
    for ext in [".png", ".jpg", ".jpeg", ".webp"]:
        parser = dp.get_parser(f"test{ext}")
        assert isinstance(parser, ImageParser)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_image_parser.py -v`
Expected: FAIL with ImportError (ImageParser not yet defined)

- [ ] **Step 3: Implement ImageParser**

In `src/knowledge/document_parser.py`, add before the `DocumentParser` class (around line 720):

```python
class ImageParser(BaseParser):
    """图片 OCR 解析器 - Qwen VL 主路径 + pytesseract 兜底"""

    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
    OCR_TIMEOUT = 30

    def __init__(
        self,
        dashscope_api_key: str = "",
        dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ):
        self._api_key = dashscope_api_key
        self._base_url = dashscope_base_url

    async def parse(self, file_path: Path, doc_id: str) -> Document:
        file_size = file_path.stat().st_size
        if file_size > self.MAX_FILE_SIZE:
            raise ValueError(f"图片过大: {file_size / 1024 / 1024:.1f}MB > {self.MAX_FILE_SIZE / 1024 / 1024}MB")

        # 主路径: Qwen VL
        if self._api_key:
            try:
                text = await asyncio.wait_for(
                    self._ocr_qwen_vl(file_path),
                    timeout=self.OCR_TIMEOUT,
                )
                return Document(
                    id=doc_id,
                    filename=file_path.name,
                    content=text,
                    metadata={"type": "image", "path": str(file_path), "file_size": file_size, "ocr_method": "qwen-vl"},
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Qwen VL OCR failed, falling back to pytesseract: {e}")

        # 兜底: pytesseract
        text = self._ocr_pytesseract(file_path)
        return Document(
            id=doc_id,
            filename=file_path.name,
            content=text,
            metadata={"type": "image", "path": str(file_path), "file_size": file_size, "ocr_method": "pytesseract"},
        )

    async def _ocr_qwen_vl(self, file_path: Path) -> str:
        """调用 Qwen VL 进行 OCR"""
        import base64
        import httpx

        image_data = file_path.read_bytes()
        ext = file_path.suffix.lstrip(".").lower()
        if ext == "jpg":
            ext = "jpeg"
        b64 = base64.b64encode(image_data).decode()

        payload = {
            "model": "qwen-vl-plus",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
                        {"type": "text", "text": "请提取这张图片中的所有文字内容。如果图片中没有文字，请描述图片的主要内容。"},
                    ],
                }
            ],
            "max_tokens": 2048,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(self.OCR_TIMEOUT)) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    def _ocr_pytesseract(self, file_path: Path) -> str:
        """本地 pytesseract OCR 兜底"""
        try:
            import pytesseract
            from PIL import Image
            image = Image.open(file_path)
            return pytesseract.image_to_string(image, lang="chi_sim+eng")
        except ImportError:
            return f"(图片文件: {file_path.name}，OCR 不可用 — 请安装 pytesseract 和 Pillow)"
        except Exception as e:
            return f"(图片 OCR 失败: {e})"

    async def chunk(self, document: Document, chunk_size: int = 500, overlap: int = 50) -> list[TextChunk]:
        """OCR 文本分块"""
        chunks: list[TextChunk] = []
        text = document.content
        start = 0
        index = 0

        while start < len(text):
            end = min(start + chunk_size, len(text))
            if end < len(text):
                last_newline = text.rfind("\n", start, end)
                if last_newline > start:
                    end = last_newline + 1
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(TextChunk(
                    id=f"{document.id}_chunk_{index}",
                    content=chunk_text,
                    metadata={**document.metadata, "chunk_index": index},
                    index=index,
                ))
                index += 1
            if end >= len(text):
                break
            new_start = end - overlap
            start = new_start if new_start > start else end

        return chunks if chunks else [TextChunk(
            id=f"{document.id}_chunk_0",
            content=document.content,
            metadata={**document.metadata, "chunk_index": 0},
            index=0,
        )]
```

- [ ] **Step 4: Register image types in DocumentParser**

In `DocumentParser.__init__`, change the constructor to accept optional DashScope config and register image types:

```python
class DocumentParser:
    """文档解析器工厂"""

    def __init__(self, dashscope_api_key: str = "", dashscope_base_url: str = ""):
        image_parser = ImageParser(
            dashscope_api_key=dashscope_api_key,
            dashscope_base_url=dashscope_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self._parsers: dict[str, BaseParser] = {
            ".md": MarkdownParser(),
            ".markdown": MarkdownParser(),
            ".txt": TextParser(),
            ".pdf": PDFParser(),
            ".docx": DOCXParser(),
            ".xlsx": XLSXParser(),
            ".xls": XLSXParser(),
            ".pptx": PPTXParser(),
            ".png": image_parser,
            ".jpg": image_parser,
            ".jpeg": image_parser,
            ".webp": image_parser,
        }
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_image_parser.py -v`
Expected: All 4 tests pass

- [ ] **Step 6: Commit**

```bash
git add src/knowledge/document_parser.py tests/test_image_parser.py
git commit -m "feat: add ImageParser with Qwen VL + pytesseract fallback"
```

---

### Task 5: Wire ImageParser config through startup

**Files:**
- Modify: `src/web/api.py`
- Modify: `src/knowledge/rag_engine.py`

- [ ] **Step 1: Pass DashScope config to DocumentParser in RAGEngine**

In `src/knowledge/rag_engine.py`, update `RAGEngine.__init__` to accept and forward DashScope config:

```python
    def __init__(
        self,
        vector_store: ChromaVectorStore | None = None,
        document_parser: DocumentParser | None = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        top_k: int = 5,
        dashscope_api_key: str = "",
        dashscope_base_url: str = "",
    ):
        self.vector_store = vector_store or get_vector_store()
        self.document_parser = document_parser or DocumentParser(
            dashscope_api_key=dashscope_api_key,
            dashscope_base_url=dashscope_base_url,
        )
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.top_k = top_k

        # 内存存储会话（生产环境应使用数据库）
        self._sessions: dict[str, ChatSession] = {}
        self._documents: dict[str, DocumentInfo] = {}
```

- [ ] **Step 2: Pass DashScope config in api.py startup**

In `src/web/api.py`, in the `startup()` function, find the `init_rag_engine()` call and add DashScope config. Find the existing call:

```python
    _rag_engine = init_rag_engine(
        persist_directory=kb_config.persist_directory,
        embedding_function=embedding_function,
        chunk_size=kb_config.chunk_size,
        chunk_overlap=kb_config.chunk_overlap,
        top_k=kb_config.top_k,
    )
```

Replace with:

```python
    # DashScope config for image OCR
    dashscope_config = _config.providers.get("dashscope")
    dashscope_api_key = dashscope_config.api_key if dashscope_config else ""
    dashscope_base_url = (dashscope_config.base_url if dashscope_config else "").replace("/api/v1", "/compatible-mode/v1")

    _rag_engine = init_rag_engine(
        persist_directory=kb_config.persist_directory,
        embedding_function=embedding_function,
        chunk_size=kb_config.chunk_size,
        chunk_overlap=kb_config.chunk_overlap,
        top_k=kb_config.top_k,
        dashscope_api_key=dashscope_api_key,
        dashscope_base_url=dashscope_base_url,
    )
```

- [ ] **Step 3: Update init_rag_engine to forward params**

In `src/knowledge/rag_engine.py`, find the `init_rag_engine` function and add the params. It should look like:

```python
def init_rag_engine(
    persist_directory: str = "data/chroma",
    embedding_function: EmbeddingFunction | None = None,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    top_k: int = 5,
    dashscope_api_key: str = "",
    dashscope_base_url: str = "",
) -> RAGEngine:
```

And pass them through to `RAGEngine(... dashscope_api_key=dashscope_api_key, dashscope_base_url=dashscope_base_url)`.

- [ ] **Step 4: Verify syntax**

Run: `python -c "import ast; ast.parse(open('src/web/api.py').read()); ast.parse(open('src/knowledge/rag_engine.py').read()); print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add src/web/api.py src/knowledge/rag_engine.py
git commit -m "feat: wire DashScope config through startup for image OCR"
```

---

### Task 6: Backend — Human-in-the-Loop feedback in orchestrator

**Files:**
- Modify: `src/coordinator/iterative_orchestrator.py`
- Modify: `src/web/api.py`

- [ ] **Step 1: Add feedback state + broadcast to IterativeOrchestrator**

In `src/coordinator/iterative_orchestrator.py`, update `__init__` to accept a broadcast callback and add feedback state:

```python
    def __init__(
        self,
        planner: TaskPlanner,
        worker_pool: WorkerPool,
        provider: LLMProvider,
        rag_engine: Any,
        model: str,
        temperature: float = 0.3,
        base_workspace: str | Path = "data/workspace",
        max_iterations: int = MAX_ITERATIONS,
        quality_threshold: float = QUALITY_THRESHOLD,
        broadcast: Any = None,
    ):
        self._planner = planner
        self._worker_pool = worker_pool
        self._provider = provider
        self._rag_engine = rag_engine
        self._model = model
        self._temperature = temperature
        self._sandbox_mgr = SandboxManager(base_workspace)
        self._max_iterations = max_iterations
        self._quality_threshold = quality_threshold
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        self._broadcast = broadcast  # async callable(dict) for WebSocket broadcast
        self._pending_feedback: dict[str, asyncio.Event] = {}
        self._feedback_content: dict[str, str] = {}
```

- [ ] **Step 2: Add submit_feedback method**

Add to the class:

```python
    def submit_feedback(self, task_id: str, feedback: str) -> bool:
        """提交用户反馈，解除等待"""
        if task_id not in self._pending_feedback:
            return False
        self._feedback_content[task_id] = feedback
        self._pending_feedback[task_id].set()
        return True

    def is_waiting_feedback(self, task_id: str) -> bool:
        """是否在等待用户反馈"""
        return task_id in self._pending_feedback and not self._pending_feedback[task_id].is_set()
```

- [ ] **Step 3: Modify _run_iterations to wait for feedback**

In `_run_iterations`, after the quality evaluation block (after `logger.info(f"[Orchestrator] 第 {iteration + 1} 轮评分: {score:.2f}")`), before the existing `if score >= self._quality_threshold:` check, add feedback wait logic:

```python
            if score >= self._quality_threshold:
                logger.info(f"[Orchestrator] 任务 {task.id} 质量达标，结束迭代")
                break

            # Human-in-the-Loop: 通知前端并等待用户反馈
            if self._broadcast:
                event = asyncio.Event()
                self._pending_feedback[task.id] = event
                try:
                    await self._broadcast({
                        "type": "task_needs_input",
                        "task_id": task.id,
                        "iteration": iteration,
                        "score": score,
                        "improvements": improvements,
                    })
                    # 等待用户反馈或超时 (120 秒)
                    try:
                        await asyncio.wait_for(event.wait(), timeout=120.0)
                        user_feedback = self._feedback_content.pop(task.id, "")
                        if user_feedback:
                            refinement_instructions = user_feedback
                            logger.info(f"[Orchestrator] 收到用户反馈: {user_feedback[:100]}")
                            continue
                    except asyncio.TimeoutError:
                        logger.info(f"[Orchestrator] 用户反馈等待超时，使用 LLM 改进建议继续")
                finally:
                    self._pending_feedback.pop(task.id, None)

            # 将改进指令传入下一轮
            refinement_instructions = "\n".join(improvements)
```

IMPORTANT: This replaces the existing `refinement_instructions = "\n".join(improvements)` line — the new code only falls through to this line if no user feedback was received.

- [ ] **Step 4: Verify syntax**

Run: `python -c "import ast; ast.parse(open('src/coordinator/iterative_orchestrator.py').read()); print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add src/coordinator/iterative_orchestrator.py
git commit -m "feat: add human-in-the-loop feedback wait in orchestrator"
```

---

### Task 7: Backend — Feedback API + WebSocket broadcast

**Files:**
- Modify: `src/web/api.py`

- [ ] **Step 1: Add WebSocket connection tracking**

At the top of api.py, near the other module-level variables (around line 70), add:

```python
_ws_connections: set[WebSocket] = set()
```

- [ ] **Step 2: Add broadcast helper function**

Add after the module-level variables:

```python
async def _ws_broadcast(data: dict) -> None:
    """广播消息到所有 WebSocket 连接"""
    import json
    message = json.dumps(data, ensure_ascii=False)
    dead: list[WebSocket] = []
    for ws in _ws_connections:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_connections.discard(ws)
```

- [ ] **Step 3: Pass broadcast to orchestrator**

In the `startup()` function, find the `_orchestrator = IterativeOrchestrator(...)` call and add the broadcast param:

```python
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
```

- [ ] **Step 4: Track WebSocket connections**

In the existing `websocket_endpoint` function, add tracking at the beginning (after `await websocket.accept()`):

```python
    _ws_connections.add(websocket)
```

And in the `except WebSocketDisconnect:` block, add cleanup:

```python
    except WebSocketDisconnect:
        _ws_connections.discard(websocket)
```

Also add cleanup in the final except:

```python
    except Exception as e:
        _ws_connections.discard(websocket)
```

- [ ] **Step 5: Add POST /api/tasks/{task_id}/feedback endpoint**

Add after the cancel endpoint:

```python
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
```

- [ ] **Step 6: Verify syntax**

Run: `python -c "import ast; ast.parse(open('src/web/api.py').read()); print('OK')"`

- [ ] **Step 7: Commit**

```bash
git add src/web/api.py
git commit -m "feat: add feedback API + WebSocket broadcast for human-in-the-loop"
```

---

### Task 8: Frontend — Feedback Modal in TasksPage

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add API method**

In the `api` object, add:

```typescript
  // 任务反馈
  submitTaskFeedback: (taskId: string, feedback: string) =>
    axios.post(`${API_BASE}/tasks/${taskId}/feedback`, { feedback }),
```

- [ ] **Step 2: Add feedback state and WebSocket handler in TasksPage**

In the `TasksPage` component, add state:

```typescript
  const [feedbackModalOpen, setFeedbackModalOpen] = useState(false);
  const [feedbackTaskId, setFeedbackTaskId] = useState('');
  const [feedbackInfo, setFeedbackInfo] = useState<any>(null);
  const [feedbackText, setFeedbackText] = useState('');
  const [submittingFeedback, setSubmittingFeedback] = useState(false);
```

Add WebSocket listener effect:

```typescript
  useEffect(() => {
    const token = localStorage.getItem('memox_token');
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws?token=${token}`;
    let ws: WebSocket | null = null;

    if (executing) {
      ws = new WebSocket(wsUrl);
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'task_needs_input') {
            setFeedbackTaskId(data.task_id);
            setFeedbackInfo(data);
            setFeedbackText('');
            setFeedbackModalOpen(true);
          }
        } catch {}
      };
    }

    return () => { ws?.close(); };
  }, [executing]);
```

Add submit handler:

```typescript
  const handleSubmitFeedback = async () => {
    if (!feedbackText.trim()) return;
    setSubmittingFeedback(true);
    try {
      await api.submitTaskFeedback(feedbackTaskId, feedbackText.trim());
      message.success('反馈已提交');
      setFeedbackModalOpen(false);
    } catch (err) {
      message.error('提交失败');
    } finally {
      setSubmittingFeedback(false);
    }
  };
```

- [ ] **Step 3: Add Modal component**

Add at the end of the TasksPage return JSX (before closing `</div>`):

```tsx
      <Modal
        title="任务需要你的指导"
        open={feedbackModalOpen}
        onCancel={() => setFeedbackModalOpen(false)}
        onOk={handleSubmitFeedback}
        okText="提交反馈"
        cancelText="跳过（自动继续）"
        confirmLoading={submittingFeedback}
      >
        {feedbackInfo && (
          <div style={{ marginBottom: 16 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Text>
                第 {(feedbackInfo.iteration || 0) + 1} 轮迭代评分：
                <Tag color={feedbackInfo.score >= 0.6 ? 'warning' : 'error'} style={{ marginLeft: 8 }}>
                  {(feedbackInfo.score * 100).toFixed(0)}%
                </Tag>
              </Text>
              {feedbackInfo.improvements?.length > 0 && (
                <div>
                  <Text type="secondary">AI 建议的改进方向：</Text>
                  <ul style={{ margin: '4px 0', paddingLeft: 20 }}>
                    {feedbackInfo.improvements.map((imp: string, i: number) => (
                      <li key={i}><Text style={{ fontSize: 13 }}>{imp}</Text></li>
                    ))}
                  </ul>
                </div>
              )}
            </Space>
            <Divider style={{ margin: '12px 0' }} />
            <Text>请输入你的指导意见（将注入下一轮迭代）：</Text>
            <TextArea
              value={feedbackText}
              onChange={e => setFeedbackText(e.target.value)}
              placeholder="例如：请重点关注代码的错误处理..."
              autoSize={{ minRows: 3, maxRows: 6 }}
              style={{ marginTop: 8 }}
            />
          </div>
        )}
      </Modal>
```

Note: `Modal`, `Divider`, `Tag`, `Space`, `TextArea` are all already imported.

- [ ] **Step 4: Build and verify**

Run: `cd /work/memoX/frontend && npm run build`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: add human-in-the-loop feedback Modal in TasksPage"
```

---

### Task 9: Final verification + build

**Files:** All modified files

- [ ] **Step 1: Run persistence tests**

Run: `pytest tests/test_persistence.py -v`
Expected: All pass

- [ ] **Step 2: Run image parser tests**

Run: `pytest tests/test_image_parser.py -v`
Expected: All pass

- [ ] **Step 3: Run document chunk tests**

Run: `pytest tests/test_document_chunks.py -v`
Expected: All pass

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v --ignore=tests/e2e -x`
Expected: All pass (or pre-existing failures only)

- [ ] **Step 5: Build frontend**

Run: `cd /work/memoX/frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 6: Commit frontend dist**

```bash
git add frontend/dist/
git commit -m "build: update frontend dist for Phase 3 features"
```

- [ ] **Step 7: Update optimization roadmap memory**

Mark items 7, 8, 9 as complete in the memory file.
