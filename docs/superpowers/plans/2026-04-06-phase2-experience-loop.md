# Phase 2 — 体验闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete Phase 2 of MemoX optimization roadmap: fix chat stream persistence gap, add session history sidebar to ChatPage, add task cancel API endpoint, and wire both into the frontend.

**Architecture:** Item 4 (Worker 智能调度) is already implemented. Item 5 needs: (a) `/api/chat/stream` must persist messages to SQLite like `/api/chat` does, (b) `/api/chat/sessions/{id}` DELETE endpoint, (c) frontend ChatPage gets a session history sidebar with create/resume/delete, (d) session title auto-generation from first user message. Item 6 needs: (a) `POST /api/tasks/{id}/cancel` endpoint wired to `IterativeOrchestrator.cancel_task`, (b) `timeout_seconds` parameter on task creation, (c) frontend cancel button + cancelled status display.

**Tech Stack:** Python/FastAPI, SQLite (existing PersistenceStore), React/Ant Design/TypeScript

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/storage/persistence.py` | Modify | Add `update_session_title()` method |
| `src/web/api.py` | Modify | Fix stream persistence, add DELETE session + POST cancel + timeout endpoints |
| `frontend/src/App.tsx` | Modify | Session sidebar in ChatPage, cancel button in TasksPage, cancelled status tag |
| `tests/test_persistence.py` | Modify | Add test for `update_session_title` |
| `tests/test_api_phase2.py` | Create | API-level tests for cancel + delete session endpoints |

---

### Task 1: Fix Chat Stream Persistence Gap

The `/api/chat/stream` endpoint (line 679 in api.py) calls `_rag_engine.add_message()` but never calls `store.save_message()`. The non-stream `/api/chat` endpoint (line 639) does both. Fix the stream endpoint to also persist.

**Files:**
- Modify: `src/web/api.py:679-751`

- [ ] **Step 1: Add persistence calls to the stream endpoint**

In `api.py`, inside the `chat_stream` function's `generate()` inner function, after the `_rag_engine.add_message(session_id, "assistant", answer)` call at line 744, add persistence:

```python
            # 持久化消息
            store = get_store()
            if store:
                store.save_message(session_id, "user", request.message)
                store.save_message(session_id, "assistant", answer)
```

Also fix `answer` — currently `content_parts` is never populated (the `on_chunk` callback is unused). The actual content comes from `response.content`. Change line 743 from:

```python
            answer = "".join(content_parts) or response.content or ""
```

to:

```python
            answer = response.content or ""
```

- [ ] **Step 2: Verify the fix manually**

Run: `python -c "import ast; ast.parse(open('src/web/api.py').read()); print('OK')"`
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add src/web/api.py
git commit -m "fix: persist chat messages in stream endpoint to SQLite"
```

---

### Task 2: Add Session Title Auto-Generation + Update Method

**Files:**
- Modify: `src/storage/persistence.py`
- Modify: `tests/test_persistence.py`

- [ ] **Step 1: Write the failing test for update_session_title**

Add to `tests/test_persistence.py`:

```python
def test_update_session_title(tmp_path):
    store = PersistenceStore(tmp_path / "test.db")
    store.save_session("s1")
    store.update_session_title("s1", "新标题")
    sessions = store.list_sessions()
    assert sessions[0]["title"] == "新标题"
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_persistence.py::test_update_session_title -v`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement update_session_title**

Add to `PersistenceStore` in `src/storage/persistence.py`, after the `save_message` method:

```python
    def update_session_title(self, session_id: str, title: str) -> None:
        """更新会话标题"""
        now = datetime.now().isoformat()
        self._conn.execute(
            "UPDATE chat_sessions SET title=?, updated_at=? WHERE id=?",
            (title, now, session_id),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_persistence.py::test_update_session_title -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/storage/persistence.py tests/test_persistence.py
git commit -m "feat: add update_session_title to PersistenceStore"
```

---

### Task 3: Add DELETE Session + Auto-Title in API

**Files:**
- Modify: `src/web/api.py`

- [ ] **Step 1: Add DELETE /api/chat/sessions/{session_id} endpoint**

Add after the `get_session_messages` endpoint (around line 677):

```python
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
```

- [ ] **Step 2: Add auto-title generation in /api/chat endpoint**

In the `/api/chat` endpoint, after persisting messages (around line 643), add title auto-generation for new sessions:

```python
    # 自动生成会话标题（取用户第一条消息前 30 字）
    if store:
        existing = store.get_session_messages(session_id)
        if len(existing) <= 2:  # 第一轮对话（刚保存的 user + assistant）
            title = request.message[:30].strip()
            store.update_session_title(session_id, title)
```

Add the same logic in the stream endpoint's `generate()` after persisting messages.

- [ ] **Step 3: Check RAG engine has delete_session**

Check if `_rag_engine.delete_session()` exists. If not, it's a dict pop — the sessions are in-memory in RAGEngine. Grep for `_sessions` or `delete_session` in `rag_engine.py`. If the method doesn't exist, add a safe call:

```python
    if _rag_engine and hasattr(_rag_engine, '_sessions'):
        _rag_engine._sessions.pop(session_id, None)
```

- [ ] **Step 4: Verify syntax**

Run: `python -c "import ast; ast.parse(open('src/web/api.py').read()); print('OK')"`

- [ ] **Step 5: Commit**

```bash
git add src/web/api.py
git commit -m "feat: add DELETE session endpoint + auto-title generation"
```

---

### Task 4: Add Task Cancel + Timeout API Endpoints

**Files:**
- Modify: `src/web/api.py`

- [ ] **Step 1: Add TaskRequest timeout field**

In the `TaskRequest` model, add:

```python
class TaskRequest(BaseModel):
    """任务请求"""
    description: str
    context: dict | None = None
    generate_suggestions: bool = True
    active_group_ids: list[str] | None = None
    timeout_seconds: int | None = None  # 任务超时（秒）
```

- [ ] **Step 2: Add POST /api/tasks/{task_id}/cancel endpoint**

Add after the `get_task` endpoint:

```python
@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    """取消正在运行的任务"""
    if not _orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")
    if _orchestrator.cancel_task(task_id):
        return {"success": True, "message": f"Task {task_id} cancel requested"}
    raise HTTPException(status_code=404, detail="Task not found or not running")
```

- [ ] **Step 3: Add timeout support to create_task**

Wrap the `_orchestrator.run()` call with `asyncio.wait_for` if `timeout_seconds` is set. Modify the `create_task` endpoint:

```python
    timeout = request.timeout_seconds
    try:
        if timeout:
            result = await asyncio.wait_for(
                _orchestrator.run(
                    description=request.description,
                    context=request.context or {},
                    active_group_ids=request.active_group_ids,
                ),
                timeout=float(timeout),
            )
        else:
            result = await _orchestrator.run(
                description=request.description,
                context=request.context or {},
                active_group_ids=request.active_group_ids,
            )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"任务执行超时（{timeout}秒）")
```

- [ ] **Step 4: Add cancelled status to task status tags**

In `get_task` response and `list_tasks`, ensure `cancelled` status is properly returned. The `IterativeOrchestrator.run()` already returns `result_summary="(任务已取消)"` on cancel. The `save_task` call should use a cancelled status. Add status detection in `create_task`:

After the existing `store.save_task` call, handle cancel detection:

```python
    # 检测取消状态
    task_status = "completed"
    if result.result_summary == "(任务已取消)":
        task_status = "cancelled"

    store = get_store()
    if store:
        store.save_task({**response_data, "description": request.description, "status": task_status})
```

Update `persistence.py`'s `save_task` to use the passed `status` instead of hardcoded `"completed"`:

```python
            (
                task_data.get("task_id", ""),
                task_data.get("description", ""),
                task_data.get("status", "completed"),  # 改为从 task_data 获取
                ...
```

- [ ] **Step 5: Verify syntax**

Run: `python -c "import ast; ast.parse(open('src/web/api.py').read()); print('OK')"`

- [ ] **Step 6: Commit**

```bash
git add src/web/api.py src/storage/persistence.py
git commit -m "feat: add task cancel endpoint + timeout support"
```

---

### Task 5: Frontend — Session History Sidebar in ChatPage

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add API methods for session management**

Add to the `api` object:

```typescript
  // 会话历史
  listSessions: () => axios.get(`${API_BASE}/chat/sessions`),
  getSessionMessages: (id: string) => axios.get(`${API_BASE}/chat/sessions/${id}/messages`),
  deleteSession: (id: string) => axios.delete(`${API_BASE}/chat/sessions/${id}`),

  // 任务取消
  cancelTask: (id: string) => axios.post(`${API_BASE}/tasks/${id}/cancel`),
```

- [ ] **Step 2: Add session sidebar to ChatPage**

Rewrite the `ChatPage` component to include a left session list. Key changes:

1. Add state for session list:
```typescript
  const [sessions, setSessions] = useState<any[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
```

2. Add fetch + create + resume + delete handlers:
```typescript
  const fetchSessions = async () => {
    setSessionsLoading(true);
    try {
      const res = await api.listSessions();
      setSessions(res.data);
    } catch (err) {
      console.error('获取会话列表失败', err);
    } finally {
      setSessionsLoading(false);
    }
  };

  const handleNewSession = () => {
    setSessionId('');
    setMessages([]);
    setSources([]);
  };

  const handleResumeSession = async (sid: string) => {
    try {
      const res = await api.getSessionMessages(sid);
      const msgs: Message[] = res.data.map((m: any, i: number) => ({
        id: `${sid}_${i}`,
        role: m.role,
        content: m.content,
      }));
      setSessionId(sid);
      setMessages(msgs);
      setSources([]);
    } catch (err) {
      message.error('恢复会话失败');
    }
  };

  const handleDeleteSession = async (sid: string) => {
    try {
      await api.deleteSession(sid);
      message.success('会话已删除');
      if (sessionId === sid) handleNewSession();
      fetchSessions();
    } catch (err) {
      message.error('删除失败');
    }
  };
```

3. Call `fetchSessions()` in useEffect and after each successful chat.

4. Wrap the ChatPage render in a flex layout with a 240px left sidebar:
```tsx
  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 120px)', gap: 16 }}>
      {/* 会话列表侧栏 */}
      <Card
        title="会话历史"
        size="small"
        style={{ width: 240, flexShrink: 0, overflowY: 'auto' }}
        extra={<Button size="small" type="primary" onClick={handleNewSession}>新对话</Button>}
      >
        <List
          loading={sessionsLoading}
          dataSource={sessions}
          locale={{ emptyText: '暂无历史会话' }}
          renderItem={(s: any) => (
            <List.Item
              style={{
                cursor: 'pointer',
                background: sessionId === s.id ? '#e6f7ff' : undefined,
                padding: '8px 12px',
              }}
              onClick={() => handleResumeSession(s.id)}
              actions={[
                <Tooltip title="删除" key="del">
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={e => { e.stopPropagation(); handleDeleteSession(s.id); }}
                  />
                </Tooltip>,
              ]}
            >
              <List.Item.Meta
                title={<Text ellipsis style={{ maxWidth: 140 }}>{s.title || '未命名会话'}</Text>}
                description={<Text type="secondary" style={{ fontSize: 11 }}>{dayjs(s.updated_at).format('MM-DD HH:mm')}</Text>}
              />
            </List.Item>
          )}
        />
      </Card>

      {/* 原有聊天主区域 */}
      <Card style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        {/* ...existing chat UI... */}
      </Card>
    </div>
  );
```

- [ ] **Step 3: Refresh session list after chat send**

In `handleSend`, after the successful API response (after `setSources(data.sources || [])` at the end of the try block), add:

```typescript
      fetchSessions();
```

- [ ] **Step 4: Build and verify**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat: add session history sidebar to ChatPage"
```

---

### Task 6: Frontend — Task Cancel Button + Cancelled Status

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add cancelled status to getStatusTag**

In the `TasksPage` component, update the `getStatusTag` config:

```typescript
      cancelled: { color: 'warning', text: '已取消' },
```

- [ ] **Step 2: Add cancel button to task execution UI**

In the task execution section, when `executing` is true, show a cancel button. This requires tracking the current task_id during execution. Since tasks are created synchronously (the API blocks until done), we need to change to a background execution model.

Change `handleExecute` to:
1. First create the task via a non-blocking approach — but the current API is synchronous. Instead, add a simple cancel mechanism: while `executing` is true, show a cancel button that hits the cancel endpoint for the most recent task.

Since the backend orchestrator registers running tasks with IDs, and the `/api/tasks/{id}/cancel` endpoint exists, but the frontend doesn't know the task_id until the request returns — we need to handle this differently.

**Approach:** Show a "取消" button that disables the executing state. The actual cancel requires knowing the task_id. For now, list running tasks from the orchestrator and add a `GET /api/tasks/running` endpoint.

Add to api.py:
```python
@app.get("/api/tasks/running")
async def list_running_tasks() -> list[str]:
    """列出正在运行的任务 ID"""
    if not _orchestrator:
        return []
    return _orchestrator.list_running_tasks()
```

In the frontend, poll for running tasks while executing:
```typescript
  const [runningTaskIds, setRunningTaskIds] = useState<string[]>([]);

  // 执行中轮询运行任务列表
  useEffect(() => {
    if (!executing) { setRunningTaskIds([]); return; }
    const interval = setInterval(async () => {
      try {
        const res = await axios.get(`${API_BASE}/tasks/running`);
        setRunningTaskIds(res.data);
      } catch {}
    }, 2000);
    return () => clearInterval(interval);
  }, [executing]);

  const handleCancel = async () => {
    for (const tid of runningTaskIds) {
      try {
        await api.cancelTask(tid);
        message.info('已请求取消任务');
      } catch {}
    }
  };
```

Add cancel button next to the "执行任务" button:
```tsx
        {executing && runningTaskIds.length > 0 && (
          <Button
            danger
            icon={<CloseCircleOutlined />}
            onClick={handleCancel}
            style={{ marginLeft: 8 }}
          >
            取消任务
          </Button>
        )}
```

- [ ] **Step 3: Build and verify**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx src/web/api.py
git commit -m "feat: add task cancel button + running tasks endpoint"
```

---

### Task 7: Mark Phase 2 Complete + Final Verification

**Files:**
- Modify: `tests/test_persistence.py` (run existing tests)

- [ ] **Step 1: Run all persistence tests**

Run: `pytest tests/test_persistence.py -v`
Expected: All tests pass

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -v --ignore=tests/e2e -x`
Expected: All tests pass (or pre-existing failures only)

- [ ] **Step 3: Build frontend**

Run: `cd frontend && npm run build`
Expected: Build succeeds

- [ ] **Step 4: Commit any remaining changes and final build output**

```bash
git add frontend/dist/
git commit -m "build: update frontend dist for Phase 2 features"
```

- [ ] **Step 5: Update optimization roadmap memory**

Mark items 4, 5, 6 as complete in the memory file.
