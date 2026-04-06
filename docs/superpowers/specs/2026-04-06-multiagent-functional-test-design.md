# 多 Agent 协作功能测试设计

**目标：** 建立两层测试体系，全面验证多 Agent 协作工作流（通信、协作、提交、测试、合并、迭代）无卡点。

**模型：** MiniMax-M2.7-highspeed（通过 MiniMaxProvider，Anthropic 兼容格式）

---

## 一、测试文件结构

```
tests/
├── test_integration_multiagent.py   # 新增：集成层（Mock LLM，纳入 CI）
└── e2e/
    ├── __init__.py
    └── test_e2e_collab.py           # 新增：E2E 层（真实 MiniMax，-m e2e 触发）
```

### 分层边界

**集成层（`test_integration_multiagent.py`）**

- LLM Provider 使用 `AsyncMock` 模拟，响应内容完全可控
- 真实运行：SandboxManager、MailBus、工具绑定、依赖解析、Merge、迭代循环
- 无网络依赖，运行时间 < 2s，纳入 CI 常规流程

**E2E 层（`tests/e2e/test_e2e_collab.py`）**

- 使用真实 `MiniMaxProvider(api_key=MINIMAX_KEY, model="MiniMax-M2.7-highspeed")`
- 两个真实 WorkerAgent：`developer` + `tester`
- 标记 `@pytest.mark.e2e`，通过 `pytest -m e2e` 单独触发，不进入 CI 常规流程
- 每个测试设置 `timeout=300s` 防止挂起

**pytest.ini 补充：**

```ini
[pytest]
markers =
    e2e: end-to-end tests requiring real API keys (deselect with '-m "not e2e"')
```

---

## 二、集成层 — 6 个测试场景

### 场景 1：`test_mailbus_communication`

**验证点：** MailBus 通信链路

Worker A 在任务执行中调用 `SendMailTool` 给 Worker B 发消息，任务结束后断言 MailBus 中 Worker B 有一条未读消息，内容匹配。

```
Worker A --[SendMailTool]--> MailBus --[ReadMailTool]--> Worker B
断言: bus.get_all("worker_b") 包含正确消息
```

### 场景 2：`test_file_collaboration`

**验证点：** 跨 Agent 文件协作

Worker A 用 `WriteFileTool` 写 `output.txt`，触发 `_merge()`，断言 `shared/worker_a/output.txt` 存在且内容正确。Worker B 用 `ReadFileTool` 读 shared 目录中该文件成功。

### 场景 3：`test_dependency_injection`

**验证点：** 子任务依赖链依赖结果注入

构造两个 SubTask：`sub_b` 的 `dependencies=["sub_a"]`。Mock Worker Pool 返回 sub_a result="A完成"。断言 sub_b 执行时收到的 `context["dependency_results"]["sub_a"] == "A完成"`。

### 场景 4：`test_merge_collects_all_outputs`

**验证点：** Merge 步骤收集全部 Agent 输出

两个 Worker 各自在沙箱写不同文件（`worker_a/a.txt`，`worker_b/b.txt`），调用 `_merge()`，断言 `shared/` 下同时存在 `worker_a/a.txt` 和 `worker_b/b.txt`。

### 场景 5：`test_refinement_hint_injected`

**验证点：** 迭代精化指令注入

LLM chat 的 `side_effect`：第 1 次返回 `score=0.5 + improvements=["修复问题A"]`，第 2 次返回 `score=0.9`。断言：
- `len(result.iterations) == 2`
- 第二轮开始前 Worker 的 `refinement_hint` 包含 "修复问题A"

### 场景 6：`test_worker_tools_bound_per_iteration`

**验证点：** 每轮迭代工具绑定完整

执行一轮迭代后，断言每个 Worker 的 `tools.list_tools()` 包含以下 6 个工具：
`read_file`、`write_file`、`list_files`、`run_shell`、`send_mail`、`read_mail`

---

## 三、E2E 层 — 渐进式场景

### 场景 1（初始实现）：全链路协作 — Python 计算器

**任务描述：**
> 创建一个 Python 计算器模块 `calculator.py`，包含 add/subtract/multiply/divide 四个函数；创建 `test_calculator.py` 用 unittest 测试这四个函数；最后用 shell 运行测试确认通过。

**Agent 分工：**

| Agent | 角色 | 职责 |
|-------|------|------|
| `developer` | 开发者 | 编写 `calculator.py`；通过 MailBus 通知 tester 文件已就绪 |
| `tester` | 测试者 | 读取 developer 消息；编写 `test_calculator.py`；用 ShellTool 运行测试；结果写入 `test_result.txt` |

**工作流全链路：**

```
[IterativeOrchestrator.run()]
  └─ plan_task()          → 生成 sub_developer + sub_tester（sub_tester 依赖 sub_developer）
  └─ create_task_workspace()
  └─ 迭代 1：
       ├─ _prepare_workers()   → 为两个 Worker 绑定沙箱工具
       ├─ _execute_with_deps() → developer 先跑，tester 后跑
       │    developer: 写 calculator.py + SendMail("tester", "代码就绪")
       │    tester:    ReadMail + 写 test_calculator.py + ShellTool(pytest) + 写 test_result.txt
       ├─ _merge()             → shared/ 汇总所有文件
       └─ _evaluate()          → LLM 评分
  └─ 若 score >= 0.8 → 结束
```

**验收断言：**

```python
assert result.final_score >= 0.8
assert len(result.iterations) >= 1
shared = Path(result.shared_dir)
assert (shared / "developer" / "calculator.py").exists()
assert (shared / "tester" / "test_result.txt").exists()
result_text = (shared / "tester" / "test_result.txt").read_text()
assert "passed" in result_text.lower() or "ok" in result_text.lower()
```

### 场景 2（待场景 1 稳定后添加）：迭代精化验证

故意给出模糊需求，预期第一轮评分低于 0.8，第二轮 refinement_hint 驱动改进，最终达标。

断言 `len(result.iterations) >= 2` 且 `result.iterations[0].score < 0.8`。

### 场景 3（后续扩展）：三节点依赖链

3 个子任务 A→B→C 串行，验证 context 在三节点间正确传递，C 的输入包含 A 和 B 的结果。

---

## 四、运行方式

```bash
# 集成层（CI 常规）
pytest tests/test_integration_multiagent.py -v

# E2E 层（手动触发）
pytest tests/e2e/ -m e2e -v -s --timeout=300

# 全量（含 E2E）
pytest -m "e2e" tests/e2e/ -v -s
```

---

## 五、API 配置

```python
# tests/e2e/test_e2e_collab.py 顶部常量
MINIMAX_API_KEY = "${MINIMAX_API_KEY}"
MODEL = "MiniMax-M2.7-highspeed"
BASE_URL = "https://api.minimaxi.com/anthropic/v1"
```

---

## 六、覆盖矩阵

| 工作流节点 | 集成层覆盖 | E2E 层覆盖 |
|-----------|-----------|-----------|
| Agent 间通信（MailBus） | 场景 1 | 场景 1（developer→tester） |
| 文件协作（沙箱读写） | 场景 2 | 场景 1（calculator.py） |
| 依赖传递（context 注入） | 场景 3 | 场景 1（sub_tester 依赖 sub_developer） |
| 输出合并（shared/） | 场景 4 | 场景 1（merge 后验证文件） |
| 迭代精化（refinement_hint） | 场景 5 | 场景 2（扩展） |
| 工具绑定完整性 | 场景 6 | 隐式（Agent 使用工具成功） |
| Shell 执行（测试运行） | — | 场景 1（pytest 运行） |
