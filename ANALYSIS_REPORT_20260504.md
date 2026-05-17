# MemoX 项目分析报告

**日期：** 2026-05-04
**代码规模：** 后端 ~14,400 行 Python + Streamlit UI ~550 行（React 实质废弃）

---

## 一、整体定位与现状

MemoX 是一个多智能体协作 RAG 平台。从 P1-P3（工程化/Alembic/RBAC）→ P4（混合检索/语义切片/引用/知识图谱）→ P5（记忆压缩/跨会话召回）→ P7（多 Agent 并行/MailBus）→ P8（Workflow DSL）大都已落地。**问题在于"接得不全"**：能力栈很厚，但许多新模块只完成了引擎层，没有真正进入请求路径。

## 二、架构评估

✅ **优点：** `src/` 12 个子包分层清晰，无循环依赖。`api.py:272-426` 启动期统一注入 `_orchestrator/_rag_engine/_memory_manager/_worker_pool` 单例，简洁可控。

⚠️ **问题：**

- `src/web/api.py` **3072 行**已是 god-file，囊括 9 大类路由（auth/documents/chat/imaging/tasks/workflows/workers/skills/ws）。建议按 APIRouter 拆分到 `src/web/routers/`。
- 前端 `frontend/src/` 只剩 `App.tsx` + 一个 `I2VModal.tsx`，README/CLAUDE.md 描述的 Chat/Tasks/Documents Ant Design 多页 SPA **从未交付**。当前实际入口是 Streamlit (`src/ui/streamlit_app.py`)。**README/CLAUDE.md 与现实严重不符。**

## 三、P4-P8 模块"通电"情况

| 模块 | 配置开关 | 入口接线 | 测试 | 状态 |
|---|---|---|---|---|
| HybridRetriever (BM25+RRF) | ✅ `hybrid_search.enabled` | ✅ RAGEngine 初始化时启用 | ✅ 较扎实 | **完整** |
| SemanticChunker | ✅ `chunk_strategy` | ✅ 解析时分流 | ✅ 较扎实 | **完整** |
| KnowledgeGraph | ✅ `enable_graph` | ⚠️ **只写不读** — 入库时建图，但 RAG search 不查图 | ✅ 仅测构图 | **半成品** |
| MemoryManager (压缩) | ⚠️ `memory.enabled` 定义但运行时**未检查** | ✅ chat_stream 注入历史 | ✅ | **flag 是装饰性** |
| MemoryRecall (跨会话) | — | ❌ 仅 `import` 未调用 | ✅ 单测有 | **死代码** |
| PreferenceLearner | — | ❌ 仅 `import` 未调用 | — | **死代码** |
| MailBus / InterAgentProtocol | — | ❌ 与 chat/task 流不连 | 浅 | **未集成** |
| WorkflowEngine | — | ✅ `/api/workflows/*` 已暴露 | 仅解析层 | **缺 E2E 测试** |
| IterativeOrchestrator | — | ✅ `/api/tasks/run` | ✅ | **完整** |

## 四、安全（🔴 优先级最高）

1. **`/api/auth/login` 无限流** — `auth.py` 暴力破解可行，其他路由都有 slowapi 限流但偏偏漏了登录。
2. **`POST /api/documents/url` 存在 SSRF** — `api.py:648-699` 仅校验 `^https?://`，未禁私网 IP（`127.0.0.1`、`169.254.169.254` 云元数据、`10/8`、`192.168/16`）。
3. **文件上传无大小上限** — `api.py:580-640` 用 `file.read()` 一次性读入内存，无 MIME 校验，10GB 文件可 OOM。
4. **`src/tools/shell.py` 黑名单沙箱不可靠** — 仅正则黑名单（`curl\s+.*http`），`$()`/反引号/管道可绕过。**仅可供受信 Agent 内部调用，绝不能走用户输入。**

## 五、性能瓶颈

- **`src/knowledge/bm25_indexer.py:103-118`：每次 `add_chunks` 全量重建 BM25**。10k chunks 分 100 次入库即 100 次重建，O(N²)。建议批量化或显式 flush。
- **`src/knowledge/knowledge_graph.py:349-380`：LLM 抽取三元组每 chunk 一次调用**，无批处理。1000 chunks ≈ 30-60s 阻塞。
- **`api.py:1679-1682` 流式响应人为切 10 字符 + 20ms sleep**，纯为视觉动画拖慢吞吐。

## 六、技术债与杂物

- `_debug_chunk.py`（仓库根，调试脚本）→ 删
- `{src/superpowers/`（字面带 `{` 前缀的畸形目录）→ 删
- `.coverage`（278KB 覆盖率产物）→ 加 `.gitignore`
- 7 张 ~450KB 截图（`mobile-*.png` / `tooltip-*.png` / `workers-*.png`）放仓库根 → 移走或瘦身
- 全代码库**几乎没有 TODO/FIXME**，14K 行如此整洁不正常，可能是 AI 生成留痕

---

# 后续改进建议（按优先级）

## 🔴 立刻修（安全相关，不可拖）

1. **加登录限流**：`@limiter.limit("5/minute")` 装饰 `/api/auth/login`。
2. **修 SSRF**：`POST /api/documents/url` 在 `httpx.get` 前先 `socket.gethostbyname` 解析 IP，用 `ipaddress.ip_address(ip).is_private/is_loopback/is_link_local` 拒绝。
3. **限上传大小**：`UploadFile.spool_max_size` + 流式写盘 + `Content-Length` 检查；建议 100MB 上限。
4. **Shell 工具改为 allowlist**：仅放行 `ls/cat/grep/python -m pytest` 等明确命令，或改用 sandbox 容器。

## 🟡 一周内（架构债，越早还越便宜）

5. **拆 `web/api.py`**：按域拆 `routers/auth.py / documents.py / chat.py / tasks.py / workflows.py / workers.py / skills.py`，每个 ~300-500 行。
6. **三件事二选一：把 KnowledgeGraph 接进检索 ／ 关闭它 ／ 删掉**。当前 `enable_graph: true` 默认开启但 `RAGEngine.search` 不查图，等于花了 LLM 抽取成本却没收益。建议在 RAGEngine 增加 `search_with_graph()` 并在 chat 流注入"实体邻居关系"作为补充上下文。
7. **MemoryRecall / PreferenceLearner 要么接入要么删除**。最小集成方案：`chat_stream` 入口调一次 `MemoryRecall.retrieve(query, user_id, top_k=3)`，把回忆塞进系统提示。Memory.enabled 配置真正生效。
8. **MailBus 决策**：要么在 `MultiAgentExecutor` 里实际用作 worker 间通信总线（替换 result aggregation 的隐式协议），要么作为 P9 留个 stub README 说明未启用。

## 🟢 两周内（性能与质量）

9. **BM25 批量重建**：暴露 `RAGEngine.flush()`；文档批量入库结束统一 `_rebuild_bm25` 一次。
10. **三元组抽取批处理**：`knowledge_graph.py` 每次发 5-10 chunk 给 LLM，要求一次返回多组三元组。
11. **去掉流式假动画**：`api.py:1679` 直接 yield LLM 原生 chunk，不要再切 10 字符。
12. **补 E2E 测试**：`tests/test_e2e_full_pipeline.py`：上传文档 → hybrid search → 多 agent 任务 → 引用回流。验证 KnowledgeGraph、MemoryRecall 真实生效。

## 🔵 文档同步

13. **README 与 CLAUDE.md 修正前端事实**：明确"主 UI 是 Streamlit，React 仅 I2V Modal 残留"，避免新人踩坑。或者把 `frontend/` 整个移除/归档。
14. **新增 `OPERATIONS.md`**：标注每个 config flag 的实际生效路径（哪些是真开关、哪些是装饰）。

## 🟣 路线图层面

15. Memory 优化清单显示 Phase 4（多租户）未启动。在做之前先把 P5/P7 接通（建议 6/7/8）— 否则多租户加在半连通的 Memory/MailBus 上会指数级放大隐患。

---

**总结**：MemoX 的代码深度（14k 行 + 12 个清晰子包）已超过同类开源项目，但**广度连接性不足**——许多新引擎已经造好，却没有"管子"通到 API 层。安全侧 4 个高危项需立即修复；功能侧的优先动作是**逐一验证每个 P4-P8 模块在请求路径上真实生效**，将"会议纪要式"完成度变成可观测的端到端能力。
