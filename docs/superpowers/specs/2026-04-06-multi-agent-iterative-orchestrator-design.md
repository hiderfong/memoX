# 多 Agent 迭代协作系统设计

**日期**：2026-04-06  
**状态**：已批准，待实现  
**目标**：支持"设计 → 开发 → 测试"全自动循环迭代，完成复杂应用开发与成套文档编写任务

---

## 1. 背景与问题

当前多 Agent 协作存在五个核心缺陷，导致无法完成需要迭代的复杂任务：

| # | 缺陷 | 位置 | 影响 |
|---|------|------|------|
| 1 | 子任务依赖结果未注入 | `task_planner.py:execute_task` | 依赖前置任务的 Agent 拿不到前置输出 |
| 2 | 无迭代精化循环 | `task_planner.py` | 任务执行一次即结束，无法"测试→修改→重测" |
| 3 | 工具全部空壳 | `src/tools/__init__.py` | Worker 无法读写文件、运行代码 |
| 4 | Worker 角色无分化 | `worker_pool.py` | 所有 Agent 用同一 prompt，无设计/开发/测试分工 |
| 5 | 无跨 Agent 共享工作区 | 整体架构 | 多 Agent 无法协作编辑同一份文档或代码库 |

---

## 2. 整体架构

### 2.1 请求处理流程

```
POST /api/tasks
      │
      ▼
IterativeOrchestrator          ← 新增，负责迭代循环
  │
  ├─ Step 1：TaskPlanner.plan_task()     ← 复用现有，分解子任务
  │
  ├─ Step 2：SandboxManager             ← 新增，为每个 Agent 创建隔离目录
  │            workspace/<task_id>/
  │              coordinator/
  │              agent_<name>/           ← 每个 Worker 的独立沙箱
  │              shared/                ← Coordinator 合并输出到此处
  │
  ├─ Step 3：WorkerPool 并行执行子任务   ← 复用现有，注入沙箱路径 + MailBus
  │            每个 WorkerAgent 拥有：
  │              - 真实工具（read_file / write_file / run_shell / send_mail / read_mail）
  │              - 独立 sandbox_dir
  │              - 共享 MailBus 引用
  │
  ├─ Step 4：Coordinator 合并            ← 新增，读取所有沙箱，合并到 shared/
  │
  ├─ Step 5：质量评估                    ← 新增，LLM 打分（0-1）
  │            score ≥ 0.8  → 完成
  │            score < 0.8  → 生成改进指令 → 回到 Step 3（最多 50 次）
  │
  └─ Step 6：返回 shared/ 最终结果 + 迭代历史
```

### 2.2 新增文件清单

| 文件 | 职责 |
|------|------|
| `src/coordinator/iterative_orchestrator.py` | 迭代主循环 |
| `src/agents/mail_bus.py` | 进程内邮件总线 |
| `src/agents/sandbox.py` | 沙箱目录管理 |
| `src/tools/filesystem.py` | read_file / write_file / list_files |
| `src/tools/shell.py` | run_shell（subprocess，限制在沙箱内） |
| `src/tools/mail.py` | send_mail / read_mail 工具包装 |

### 2.3 现有文件改动

| 文件 | 改动范围 |
|------|---------|
| `src/agents/worker_pool.py` | 增加 `tool_factories` + `sandbox_dir` 参数 |
| `src/coordinator/task_planner.py` | 依赖结果注入逻辑移入 IterativeOrchestrator |
| `src/web/api.py` | 注册真实工具；`/api/tasks` 切换到 IterativeOrchestrator |

---

## 3. MailBus（进程内邮件总线）

### 3.1 数据模型

```python
@dataclass
class MailMessage:
    id: str                  # 消息唯一 ID（uuid hex）
    from_agent: str          # 发件人 agent name
    to_agent: str            # 收件人 agent name（"coordinator" 合法）
    subject: str             # 主题
    body: str                # 正文
    attachments: list[str]   # 文件路径列表（沙箱内绝对路径，只传引用不复制文件）
    created_at: str
    read: bool = False
```

### 3.2 接口

```python
class MailBus:
    def send(self, from_agent, to_agent, subject, body, attachments=[]) -> str
    def read_inbox(self, agent_name) -> list[MailMessage]   # 仅未读
    def mark_read(self, message_id) -> None
    def get_all(self, agent_name) -> list[MailMessage]      # 含已读
```

### 3.3 实现要点

- **存储**：进程内字典，`asyncio.Lock` 保护并发读写，任务结束即销毁
- **跨沙箱文件访问**：`attachments` 只存路径引用；接收方用 `read_file` 工具读取，路径可跨沙箱读（只读），不可跨沙箱写
- **`coordinator` 作为特殊收件人**：Worker 可向 Coordinator 汇报进度或请求澄清

### 3.4 典型通信场景

```
设计 Agent   → 开发 Agent：  "架构文档已就绪，见 attachments: [design.md]"
开发 Agent   → 测试 Agent：  "模块 A 已写完，路径 agent_code_worker/src/a.py"
测试 Agent   → coordinator： "发现 3 处问题，见 bug_report.md"
coordinator  → 开发 Agent：  "第 2 轮：请修复 bug_report.md 中列出的问题"
```

---

## 4. 沙箱管理与真实工具

### 4.1 SandboxManager

```
workspace/
  <task_id>/
    coordinator/             ← Coordinator 工作区
    agent_code_worker/       ← 按 WorkerConfig.name 命名
    agent_research_worker/
    shared/                  ← 最终合并输出
```

```python
class SandboxManager:
    def create_task_workspace(self, task_id: str) -> Path
    def get_agent_sandbox(self, task_id: str, agent_name: str) -> Path  # 自动 mkdir
    def get_shared_dir(self, task_id: str) -> Path
    def cleanup(self, task_id: str) -> None   # 任务完成后可选清理
```

### 4.2 真实工具规格

| 工具名 | 参数 | 读权限 | 写权限 |
|--------|------|--------|--------|
| `read_file` | `path` | 自身沙箱 + shared/ + 其他 Agent 沙箱 | — |
| `write_file` | `path, content` | — | 仅自身沙箱 |
| `list_files` | `path?` | 自身沙箱（默认根目录） | — |
| `run_shell` | `command, timeout?` | cwd = 自身沙箱 | 仅自身沙箱 |
| `send_mail` | `to, subject, body, attachments?` | — | 写入 MailBus |
| `read_mail` | — | 读取自身收件箱 | 标记已读 |

### 4.3 Shell 工具安全限制

```python
BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",      # 禁止删根目录
    r"curl\s+.*http",      # 禁止外网请求（可配置开放）
    r"wget\s+.*http",
    r">\s*/etc/",          # 禁止写系统目录
    r"sudo",
    r"chmod\s+777",
]

DEFAULT_TIMEOUT = 60      # 秒，可通过参数调整
MAX_TIMEOUT = 300         # 秒，硬上限
```

stdout + stderr 合并返回给 Agent。

---

## 5. IterativeOrchestrator

### 5.1 常量

```python
MAX_ITERATIONS = 50        # 兜底上限，防止复杂任务过早终止
QUALITY_THRESHOLD = 0.8    # Coordinator 评分达到此值即停止迭代
```

### 5.2 核心流程

```python
async def run(self, description, context, active_group_ids=None) -> IterationResult:

    # Step 1：RAG 检索（复用现有逻辑）
    context = await self._inject_rag_context(description, context, active_group_ids)

    # Step 2：任务规划
    task, complexity = await self._planner.plan_task(description, context)

    # Step 3：创建沙箱 + MailBus
    sandbox_mgr = SandboxManager(self._base_workspace)
    sandbox_mgr.create_task_workspace(task.id)
    mail_bus = MailBus(task_id=task.id)

    history: list[IterationRecord] = []
    refinement_instructions = ""

    for iteration in range(MAX_ITERATIONS):

        # Step 4：绑定工具（动态注入沙箱路径）
        self._prepare_workers(task, sandbox_mgr, mail_bus, refinement_instructions)

        # Step 5：执行（含依赖结果注入）
        await self._execute_with_deps(task, context, mail_bus)

        # Step 6：合并沙箱 → shared/
        merged_summary = await self._merge(task, sandbox_mgr)

        # Step 7：质量评估
        score, improvements = await self._evaluate(description, merged_summary, iteration)
        history.append(IterationRecord(iteration, score, improvements))

        if score >= QUALITY_THRESHOLD:
            break

        # 将改进指令传入下一轮
        refinement_instructions = "\n".join(improvements)

    return IterationResult(
        task_id=task.id,
        shared_dir=str(sandbox_mgr.get_shared_dir(task.id)),
        final_score=score,
        iterations=history,
    )
```

### 5.3 依赖结果注入（修复当前关键 Bug）

当前 `task_planner.py` 所有子任务共享同一 `context`，依赖任务的输出从不传入后续任务。修复后：

```python
async def _execute_with_deps(self, task, base_context, mail_bus):
    completed: dict[str, str] = {}   # subtask_id → result
    pending = list(task.sub_tasks)

    while pending:
        # 依赖全部完成的子任务可以立即执行
        ready = [st for st in pending if all(d in completed for d in st.dependencies)]
        if not ready:
            raise RuntimeError("循环依赖或死锁")

        # 为每个就绪任务构建独立 context（注入依赖结果）
        per_task_ctx = {
            st.id: {
                **base_context,
                "dependency_results": {d: completed[d] for d in st.dependencies},
            }
            for st in ready
        }

        results = await self._worker_pool.execute_parallel(ready, per_task_ctx)
        for st, result, error in results:
            completed[st.id] = result or error or ""
            pending.remove(st)
```

### 5.4 质量评估 Prompt

```
你是 Coordinator，评估以下任务的完成质量。

原始需求：{description}
当前输出摘要（shared/ 目录内容）：{merged_summary}
迭代轮次：{iteration + 1} / {MAX_ITERATIONS}

请返回 JSON：
{
  "score": 0.0-1.0,
  "passed": true/false,
  "improvements": ["具体改进点1", "改进点2"]
}

评分标准：
- 0.0-0.4：严重缺失，主要功能未实现
- 0.4-0.7：基本完成，但有明显不足
- 0.7-0.8：大体满足需求，有少量问题
- 0.8-1.0：高质量完成，可以接受
```

---

## 6. 工具动态绑定机制

工具实例在每次迭代开始前由 `_prepare_workers()` 创建并注入 `ToolRegistry`，本次迭代结束后清空，下次迭代重新绑定。

```python
def _prepare_workers(self, task, sandbox_mgr, mail_bus, refinement_instructions):
    for subtask in task.sub_tasks:
        worker = self._worker_pool.get_worker_for(subtask)
        if not worker:
            continue

        sandbox_dir = sandbox_mgr.get_agent_sandbox(task.id, worker.config.name)
        registry = ToolRegistry()

        # 绑定沙箱路径的文件系统工具
        registry.register(ReadFileTool(sandbox_dir, task.id, sandbox_mgr))
        registry.register(WriteFileTool(sandbox_dir))
        registry.register(ListFilesTool(sandbox_dir))

        # Shell 工具（cwd 锁定在沙箱）
        registry.register(ShellTool(cwd=sandbox_dir))

        # 邮件工具（绑定 agent_name + mail_bus）
        registry.register(SendMailTool(worker.config.name, mail_bus))
        registry.register(ReadMailTool(worker.config.name, mail_bus))

        worker.tools = registry

        # 将改进指令追加到系统 prompt
        if refinement_instructions:
            worker.refinement_hint = refinement_instructions
```

---

## 7. API 响应变更

`POST /api/tasks` 响应新增字段：

```json
{
  "task_id": "task_abc123",
  "complexity": "parallel",
  "result": "任务完成摘要",
  "shared_dir": "workspace/task_abc123/shared",
  "final_score": 0.84,
  "iterations": [
    {
      "iteration": 0,
      "score": 0.61,
      "improvements": ["缺少错误处理", "文档章节不完整"]
    },
    {
      "iteration": 1,
      "score": 0.84,
      "improvements": []
    }
  ],
  "suggestions": []
}
```

---

## 8. 典型使用场景

### 场景 A：复杂应用开发

```
任务："开发一个 Todo REST API，包含增删改查和单元测试"

子任务（顺序执行）：
  1. 设计 Agent：输出 API 设计文档（design.md）
  2. 开发 Agent：读取 design.md，实现 FastAPI 代码
  3. 测试 Agent：读取代码，编写并运行 pytest

迭代：
  轮 1 → 测试 Agent 发现接口缺少分页 → score=0.62
  轮 2 → 开发 Agent 收到邮件修复 → 测试全通过 → score=0.91 → 结束
```

### 场景 B：成套文档编写

```
任务："为 MemoX 编写完整用户手册，包含安装、配置、API 参考、FAQ"

子任务（并行执行）：
  - 安装文档 Agent：输出 install.md
  - 配置文档 Agent：输出 config.md
  - API 参考 Agent：输出 api-reference.md
  - FAQ Agent：输出 faq.md

合并后 Coordinator 评估一致性 → 发现交叉引用错误 → 发邮件给各 Agent 修正
轮 2 → score=0.88 → 结束，shared/ 包含完整文档集
```

---

## 9. 实现顺序建议

1. `src/agents/mail_bus.py` — 无依赖，先实现
2. `src/agents/sandbox.py` — 无依赖，先实现
3. `src/tools/filesystem.py` — 依赖 sandbox
4. `src/tools/shell.py` — 依赖 sandbox
5. `src/tools/mail.py` — 依赖 mail_bus
6. `src/coordinator/iterative_orchestrator.py` — 依赖以上所有
7. `src/agents/worker_pool.py` — 小改，增加 tool_factories
8. `src/web/api.py` — 最后，接入 orchestrator
