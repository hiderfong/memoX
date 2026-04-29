# MemoX 开发计划 (P4-P8)

> 基于竞品分析（Langchain-Chatchat、RAGFlow、open-webui、mem0、Dify）制定。  
> P1-P3 已完成（工程化、Alembic、RBAC/Audit/RateLimiting）。

---

## P4 — RAG 深度化

**目标**：让知识检索从"向量相似度匹配"升级为"混合检索 + 结构化切片 + 可溯源引用"。

### P4-1：混合检索（Hybrid Search）

**现状**：`rag_engine.py` 仅用向量相似度检索，无全文搜索。

**改动**：
- `src/knowledge/hybrid_retriever.py`（新文件）
  - BM25 全文索引：用 `rank_bm25` 库对所有 chunk 建索引
  - `HybridRetriever.search(query, k=10)` → 返回 `list[Chunk]` 并集 + RRF 融合排序
  - `data/chunks_bm25.pkl`（pickle 文件）持久化 BM25 索引
- `src/knowledge/rag_engine.py`：
  - `RAGEngine.search()` 改为调用 `HybridRetriever`，而非直接查 ChromaDB
  - 新增 `rebuild_bm25_index()` 方法，文档增删时增量更新

**文件改动**：`src/knowledge/hybrid_retriever.py`（新增）、`src/knowledge/rag_engine.py`（修改）

**依赖**：`rank_bm25>=0.2`

### P4-2：语义切片（Semantic Chunking）

**现状**：`document_parser.py` 的 `chunk_by_size()` 固定长度切片，破坏语义完整性。

**改动**：
- `src/knowledge/semantic_chunker.py`（新文件）
  - `SemanticChunker.chunk(document)`：
    1. 用轻量 LLM（embedding provider）给每个句子打"主题边界"分数
    2. 合并相邻同类主题句子，直到超过 `max_tokens` 阈值才断开
  - 回退方案：`max(theme_score) - min(theme_score) > threshold` 时断句
- `src/knowledge/document_parser.py`：
  - `parse_and_chunk(file_path, ...)` 增加 `chunk_strategy: Literal["size", "semantic"] = "semantic"` 参数
  - 兼容旧策略，新切片存入 ChromaDB 时标注 `chunk_strategy` 元数据

**文件改动**：`src/knowledge/semantic_chunker.py`（新增）、`src/knowledge/document_parser.py`（修改）

**依赖**：无新依赖（复用现有 embedding provider）

### P4-3：引用来源高亮（Citation Tracking）

**现状**：RAG 返回 chunk 文本，但前端无法标注"这个答案来自哪篇文档的哪一段"。

**改动**：
- `src/knowledge/rag_engine.py`：
  - `RAGEngine.search()` 返回结构升级为 `list[RetrievedChunk]`，含 `doc_id`、`chunk_index`、`page_number`（如有）、`text`
  - 抽取答案中出现的关键句，映射回对应 chunk，标记 `cited=True`
- `src/web/api.py`：
  - `/api/chat` 和 `/api/chat/stream` 响应体增加 `citations: list[dict]` 字段：
    ```json
    {
      "text": "答案主体...",
      "citations": [
        {"doc_id": "abc123", "doc_name": "年度财报.pdf", "chunk_text": "关键段落...", "page": 3}
      ]
    }
    ```
- `frontend/src/pages/Chat.tsx`：
  - 流式渲染时，在答案底部或侧边展示引用来源列表
  - 点击引用高亮对应原文（需要 `GET /api/documents/{doc_id}/chunks/{chunk_id}`）

**文件改动**：`src/knowledge/rag_engine.py`（修改）、`src/web/api.py`（修改）、`frontend/src/pages/Chat.tsx`（修改）

### P4-4：知识图谱构建（Knowledge Graph）— 实验性

**现状**：无知识图谱能力。

**改动**：
- `src/knowledge/knowledge_graph.py`（新文件）
  - `KnowledgeGraph.build_from_documents(chunks)`：
    - 用 LLM 抽取 `<subject, predicate, object>` 三元组
    - 存入 `data/knowledge_graph.gml`（NetworkX `MultiDiGraph`）
  - `KnowledgeGraph.query(subgraph_of entity)`：查询某个实体相关的子图，返回关联路径
  - `KnowledgeGraph.search(query)`：模糊匹配实体名，返回相关三元组
- `src/knowledge/rag_engine.py`：
  - 新增 `RAGEngine.search_with_graph(query)` → 混合（向量 + 图谱）结果

**文件改动**：`src/knowledge/knowledge_graph.py`（新增）、`src/knowledge/rag_engine.py`（修改）

**依赖**：`networkx>=3.0`

**注意**：此功能为实验性，可通过 `config.yaml` 开关 `knowledge_base.enable_graph: true/false`

---

## P5 — 记忆与上下文管理

**目标**：让 Agent 拥有"长期记忆"，而非每次对话都是白板。

### P5-1：对话摘要与记忆压缩

**现状**：`persistence.py` 存储完整对话历史，但 Agent 每次都注入全量历史（token 浪费且有上限）。

**改动**：
- `src/memory/memory_manager.py`（新文件）
  - `MemoryManager.compress_session(session_id)`：
    - 超过 `config.memory.max_turns_before_compress` 轮时触发（建议默认 10 轮）
    - 用 LLM 抽取"关键事实、用户偏好、未完成事项"，生成 200 字摘要
    - 摘要存入 `sessions.summary` 字段（`persistence.py` 新增该列）
    - 原始消息保留但标记 `archived=True`，不注入上下文
  - `MemoryManager.get_context(session_id)`：返回摘要 + 最近 N 轮未归档消息
- `src/coordinator/iterative_orchestrator.py`：
  - 执行前调用 `memory_manager.get_context()` 获取压缩后上下文
  - 任务完成后检查是否触发压缩

**文件改动**：`src/memory/memory_manager.py`（新增）、`src/coordinator/iterative_orchestrator.py`（修改）、`alembic/versions/0003_add_session_summary.py`（迁移）

### P5-2：跨会话记忆召回（Cross-Session Memory）

**现状**：Agent 只知道当前会话历史，无法利用"用户上次让修的 bug"这类信息。

**改动**：
- `src/memory/memory_recall.py`（新文件）
  - `MemoryRecall.index(memories)`：将记忆摘要向量化，存入独立 ChromaDB collection `memory_recall`
  - `MemoryRecall.retrieve(query, user_id, top_k=5)`：检索相关记忆，注入 Agent 上下文
  - 记忆来源：任务完成后的 `OptimizationSuggestions`、用户明确标记的"重要信息"、摘要中的关键事实
- `src/web/api.py`：
  - `/api/memories` GET：获取当前用户的记忆列表
  - `/api/memories` POST：用户手动添加记忆（类似笔记）
  - `/api/memories/{id}` DELETE
- `src/storage/persistence.py`：
  - 新表 `memories(id, user_id, content, embedding_id, created_at, is_auto_generated)`

**文件改动**：`src/memory/memory_recall.py`（新增）、`src/memory/memory_manager.py`（扩展）、`src/web/api.py`（新增路由）、`src/storage/persistence.py`（新表）、`alembic/versions/0004_add_memories.py`（迁移）

### P5-3：用户偏好学习

**改动**：
- `src/memory/preference_learner.py`（新文件）
  - `PreferenceLearner.infer_from_history(user_id)`：分析历史对话，提取用户的写作风格、技术偏好、响应格式偏好
  - 结果存入 `sessions.preferences`（JSON 字段）
  - Agent 每次执行前将偏好作为隐式 system prompt 注入

---

## P6 — Web UI 现代化

**目标**：补全前端能力，让非技术用户也能顺畅使用 MemoX。

### P6-1：轻量 UI 原型（Streamlit）

**若前端能力较弱**，先用 Streamlit 出一个管理界面（3天可完成）：

```
# 快速验证用，后期替换为 React
src/ui/streamlit_app.py
```

**功能**：
- 上传文档（拖拽），显示解析进度和切片数量
- 知识库搜索测试框（输入 query，看检索结果和来源）
- Worker 状态面板（在线/离线/任务数/Token 消耗）
- 记忆管理（查看/添加/删除跨会话记忆）

**文件改动**：`src/ui/streamlit_app.py`（新增）、`pyproject.toml` 新增 `streamlit` 依赖

**依赖**：`streamlit>=1.30`

### P6-2：现有前端增强（React）

**基于 `frontend/src/` 现有代码**：

- `frontend/src/pages/Chat.tsx`：
  - 引用来源高亮（来自 P4-3）
  - 对话导出（Markdown / PDF）
  - 消息引用跳转（点击引用滚动到对应上下文）
- `frontend/src/pages/Documents.tsx`：
  - 上传进度条（分片上传 + 解析状态）
  - 文档预览（.pdf / .docx 在线预览，不需要下载）
  - 切片结果预览（每个文档切了多少块、策略是什么）
- `frontend/src/pages/Workers.tsx`：
  - Token 消耗可视化（饼图 / 柱状图）
  - Worker 日志查看（实时 tail，但限制最后 100 行）
- `frontend/src/pages/Settings.tsx`：
  - API Key 可视化配置（遮蔽显示，可切换显示/隐藏）
  - 记忆开关（开启/关闭跨会话记忆、偏好学习）

---

## P7 — Multi-Agent 协作

**目标**：从单 Agent 迭代升级为"多 Agent 并行 + 结果聚合"。

### P7-1：并行子 Agent 调度

**改动**：
- `src/coordinator/multi_agent_executor.py`（新文件）
  - `MultiAgentExecutor.execute_parallel(task, workers: list[Worker])`：
    - 将 task 拆分为多个 `SubTask`，分配给不同 Worker 同时执行
    - 用 `asyncio.gather()` 并发调用
    - 收集所有结果后调用 `ResultAggregator.aggregate(results)`
  - `ResultAggregator.aggregate(results)`：用 LLM 将多个 Worker 结果融合为一个连贯答案
- `src/coordinator/iterative_orchestrator.py`：
  - 新增 `mode: Literal["iterative", "parallel"]` 参数
  - `parallel` 模式：调用 `MultiAgentExecutor` 替代原有循环

**文件改动**：`src/coordinator/multi_agent_executor.py`（新增）、`src/coordinator/iterative_orchestrator.py`（扩展）

### P7-2：Agent 间通信协议

**现状**：`Worker` 输出 `TaskResult`，但没有标准化格式供其他 Worker 消费。

**改动**：
- `src/agents/inter_agent_protocol.py`（新文件）
  - 定义 `InterAgentMessage` 结构：
    ```python
    @dataclass
    class InterAgentMessage:
        sender: str          # worker_id
        receiver: str | None # worker_id or None (broadcast)
        content: str         # message body
        attachments: list[ToolResult]  # 上游 Tool 输出
        reply_to: str | None # in_reply_to message_id
        priority: int        # 1-5, higher = more urgent
    ```
  - `MailBus.broadcast(msg)` / `MailBus.send_to(worker_id, msg)` 已有实现（`mail_bus.py`），只需增加 `InterAgentMessage` 支持

---

## P8 — 低代码 Workflow 编排（长远目标）

**目标**：让非程序员也能编排 Agent 执行流程。

### P8-1：Workflow DSL 定义

**改动**：
- `src/workflow/dsl.py`（新文件）
  - 定义 YAML schema：
    ```yaml
    workflow:
      name: "研究报告生成"
      description: "搜索 + 写作 + 校对三阶段"
      steps:
        - id: search
          worker: researcher
          input: "${query}"
          output: "search_results"
          condition: "always"
        - id: write
          worker: writer
          input: "${search_results}"
          output: "draft"
          condition: "${search_results.relevant}"
        - id: review
          worker: reviewer
          input: "${draft}"
          output: "final"
          condition: "always"
    ```
- `src/workflow/workflow_parser.py`（新文件）
  - 解析 YAML → `Workflow` dataclass
  - 验证 `worker` 字段是否为已注册 Worker
  - 生成执行计划 DAG

### P8-2：Workflow 执行引擎

**改动**：
- `src/workflow/engine.py`（新文件）
  - `WorkflowEngine.execute(workflow: Workflow, context: dict)`：
    - 按 DAG 拓扑序执行
    - 条件跳过（`condition` 字段）
    - 并行执行无依赖节点（类似 P7-1 的并行调度）
    - 中途可暂停（状态存 SQLite），支持 `resume`

### P8-3：可视化编辑器（参考 Dify）

**长期目标**，不在本计划详细展开。

---

## 依赖总览

| 依赖 | 版本 | 用途 | 引入阶段 |
|------|------|------|---------|
| `rank_bm25` | `>=0.2` | 全文检索 | P4-1 |
| `networkx` | `>=3.0` | 知识图谱 | P4-4 |
| `streamlit` | `>=1.30` | 轻量 UI 原型 | P6-1 |

其余均复用现有依赖。

---

## Alembic 迁移计划

| 迁移 | 内容 | 阶段 |
|------|------|------|
| `0003_add_session_summary.py` | `sessions.summary`，`sessions.archived` | P5-1 |
| `0004_add_memories.py` | 新表 `memories` | P5-2 |
| `0005_add_memory_preferences.py` | `sessions.preferences` JSON | P5-3 |
| `0006_add_workflow_tables.py` | `workflows`，`workflow_runs` | P8 |

---

## 测试计划

| 阶段 | 测试策略 |
|------|---------|
| P4 | 新增 `tests/test_hybrid_retriever.py`、`tests/test_semantic_chunker.py`、`tests/test_knowledge_graph.py`，复用现有 RAG 集成测试 |
| P5 | 新增 `tests/test_memory_manager.py`、`tests/test_memory_recall.py` |
| P6 | 前端 E2E Playwright 测试（上传文档流程、引用高亮点击） |
| P7 | 新增 `tests/test_multi_agent_executor.py` |
| P8 | 新增 `tests/test_workflow_parser.py`、`tests/test_workflow_engine.py` |

---

## 优先级与工作量估算

| 阶段 | 优先级 | 工作量 | 关键依赖 |
|------|--------|--------|---------|
| **P4-1** 混合检索 | ★★★★ | 2-3d | rank_bm25 |
| **P4-3** 引用高亮 | ★★★★ | 2-3d | P4-1 |
| **P4-2** 语义切片 | ★★★ | 3-4d | embedding provider |
| **P5-1** 记忆压缩 | ★★★★ | 3-4d | persistence.py 改造 |
| **P5-2** 跨会话记忆 | ★★★ | 3-4d | P5-1 |
| **P6-2** 前端增强 | ★★★ | 5-7d | P4-3 完成后 |
| **P6-1** Streamlit 原型 | ★★ | 2-3d | 可选，快速验证 |
| **P7-1** 并行 Agent | ★★★ | 3-4d | 复用现有 WorkerPool |
| **P7-2** 通信协议 | ★★ | 2-3d | P7-1 |
| **P4-4** 知识图谱 | ★★ | 4-5d | networkx，实验性 |
| **P5-3** 偏好学习 | ★ | 2-3d | P5-2 |
| **P8** Workflow DSL | ★ | 7-10d | 长期目标 |

---

## 建议实施顺序

```
P4-1 (混合检索)
    ↓
P4-3 (引用高亮) ← 需要 P4-1 的检索结构
    ↓
P5-1 (记忆压缩) ← 与 P4 并行，P5-1 独立
    ↓
P5-2 (跨会话记忆)
    ↓
P6-2 (前端增强) ← 依赖 P4-3
P7-1 (并行 Agent) ← 独立于 P4/P5
P4-4 (知识图谱) ← 实验性，随时可暂停
P5-3 (偏好学习) ← P5-2 之后
P6-1 (Streamlit) ← 可在任何阶段插入验证
P7-2 (通信协议) ← P7-1 之后
P8 (Workflow DSL) ← P7 完全稳定后
```
