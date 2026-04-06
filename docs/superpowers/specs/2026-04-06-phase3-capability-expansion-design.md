# Phase 3 — 能力扩展 Design Spec

## Overview

Phase 3 adds three capabilities to MemoX: document preview with full-text search, image OCR (Qwen VL + pytesseract fallback), and human-in-the-loop task intervention.

## Item 7: 文档预览与全文搜索

### Backend

**`GET /api/documents/{doc_id}/chunks`** — Returns all chunks for a document from ChromaDB.

- Query ChromaDB with `where={"doc_id": doc_id}` filter
- Return `{ "doc_id": str, "filename": str, "chunks": [{ "id": str, "content": str, "index": int }] }`
- Needs a new method on `ChromaVectorStore`: `get_chunks_by_doc(doc_id, collection_name)` that does a `collection.get(where={"doc_id": doc_id})`

**`GET /api/documents/search?q=keyword&group_ids=id1,id2`** — Full-text keyword search across all documents.

- Uses existing `RAGEngine.search()` with the query string
- Returns matches with `doc_id`, `filename`, `chunk_content` (highlighted), `score`
- The `group_ids` param is optional comma-separated filter
- Response: `{ "query": str, "results": [{ "doc_id": str, "filename": str, "content": str, "score": float, "chunk_index": int }] }`

### Frontend

**Document detail Drawer** — Click document name in the list to open an Ant Design `Drawer` showing:
- Document metadata (filename, type, size, chunk count, created_at)
- Scrollable list of all chunks with index numbers
- Each chunk displayed in a light Card

**Search bar** — Added above the document list in DocumentsPage:
- `Input.Search` component, calls `/api/documents/search?q=...`
- Results displayed as a list replacing the document list, with "返回" button to go back
- Each result shows filename, matched chunk snippet, relevance score

## Item 9: 图片 OCR

### ImageParser

New parser class in `src/knowledge/document_parser.py` for `.png`, `.jpg`, `.jpeg`, `.webp`.

**Primary: Qwen VL via DashScope**
- Endpoint: `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- Model: `qwen-vl-plus`
- Auth: `Authorization: Bearer {dashscope_api_key}` (from config.yaml, already configured)
- Request: OpenAI-compatible format with `image_url.url` = `data:image/{ext};base64,{b64data}`
- Prompt: `"请提取这张图片中的所有文字内容。如果图片中没有文字，请描述图片的主要内容。"`
- Timeout: 30s

**Fallback: pytesseract**
- When Qwen VL call fails (network error, timeout, API error)
- Uses `pytesseract.image_to_string(image, lang='chi_sim+eng')`
- Requires system `tesseract-ocr` + language packs installed

**Integration:**
- `ImageParser.parse()` tries Qwen VL first, falls back to pytesseract, returns `Document` with OCR text as content
- Register in `DocumentParser.__init__`: `.png`, `.jpg`, `.jpeg`, `.webp` → `ImageParser`
- Config: `ImageParser` reads DashScope API key from config at construction time (passed in from api.py startup)

### Config Changes

No new config keys needed — reuses existing `providers.dashscope.api_key` and `providers.dashscope.base_url`. The `ImageParser` constructor takes `api_key` and `base_url` as params.

`DocumentParser` constructor gains optional `dashscope_api_key` and `dashscope_base_url` params, passed through to `ImageParser`.

In `api.py` startup, when creating `RAGEngine`, pass the DashScope config to the document parser.

## Item 8: Human-in-the-Loop

### Backend Changes

**`IterativeOrchestrator` modifications:**

In `_run_iterations()`, after `_evaluate()` returns a score below threshold:
1. Broadcast a WebSocket event: `{"type": "task_needs_input", "task_id": str, "iteration": int, "score": float, "improvements": [str]}`
2. Wait on an `asyncio.Event` with a timeout (default 120s)
3. If user provides feedback before timeout, inject it as `refinement_instructions` for next iteration
4. If timeout expires, continue with LLM-generated improvements as before

**New state in orchestrator:**
- `_pending_feedback: dict[str, asyncio.Event]` — task_id → event
- `_feedback_content: dict[str, str]` — task_id → user feedback text

**`POST /api/tasks/{task_id}/feedback`**
- Request body: `{"feedback": str}`
- Sets `_feedback_content[task_id]` and triggers `_pending_feedback[task_id].set()`
- Returns `{"success": True}`
- Returns 404 if task is not waiting for feedback

**WebSocket broadcast:**
- Add a module-level `_ws_connections: set[WebSocket]` in api.py
- On WebSocket connect, add to set; on disconnect, remove
- Orchestrator receives a `broadcast` callback at construction time
- When needing input, calls `broadcast(event_dict)` which sends to all connected WebSocket clients

### Frontend Changes

**TasksPage modifications:**

When the WebSocket receives a `task_needs_input` event during task execution:
1. Show a Modal with the current score, improvement suggestions, and a TextArea for user feedback
2. User types feedback and clicks "提交反馈"
3. Frontend calls `POST /api/tasks/{task_id}/feedback` with the feedback text
4. Modal closes, task continues

If user dismisses the Modal without feedback, the orchestrator timeout will trigger and continue automatically.

**New API method:**
```
submitTaskFeedback: (taskId: string, feedback: string) => axios.post(`${API_BASE}/tasks/${taskId}/feedback`, { feedback })
```

### WebSocket Event Flow

```
Orchestrator evaluates → score < threshold
    ↓
Broadcast via WS: {"type": "task_needs_input", "task_id": "xxx", "score": 0.6, "improvements": [...]}
    ↓
Frontend shows Modal with feedback input
    ↓
User submits → POST /api/tasks/{task_id}/feedback
    ↓
Orchestrator receives feedback → injects as refinement_instructions → next iteration
```

## File Changes Summary

| File | Changes |
|---|---|
| `src/knowledge/vector_store.py` | Add `get_chunks_by_doc()` method |
| `src/knowledge/document_parser.py` | Add `ImageParser` class, register in `DocumentParser` |
| `src/knowledge/rag_engine.py` | Add `get_document_chunks()` wrapper |
| `src/coordinator/iterative_orchestrator.py` | Add feedback wait logic, broadcast callback |
| `src/web/api.py` | Add 4 endpoints, WS broadcast, pass dashscope config to parser |
| `frontend/src/App.tsx` | Document Drawer, search bar, feedback Modal, new API methods |

## Dependencies

- `pytesseract` + system `tesseract-ocr` (optional, fallback OCR)
- `Pillow` (for image loading in pytesseract path)
- No new npm dependencies
