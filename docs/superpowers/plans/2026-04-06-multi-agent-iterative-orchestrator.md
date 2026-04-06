# 多 Agent 迭代协作系统实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 MailBus、沙箱管理、真实工具（文件/Shell/邮件）、依赖注入与质量评估迭代的多 Agent 协作系统，修复依赖传递 Bug 并替换现有 `/api/tasks` 执行逻辑。

**Architecture:** 新增 `IterativeOrchestrator` 作为迭代主循环（最多 50 次迭代，质量阈值 0.8）。每次迭代前动态为每个 WorkerAgent 绑定沙箱路径的真实工具；MailBus 作进程内邮件总线让 Agent 间异步通信；SandboxManager 为每个任务和 Agent 创建隔离目录；依赖注入通过 per-task context 字典修复现有 Bug。

**Tech Stack:** Python 3.12, asyncio, FastAPI, pathlib, subprocess（带超时和 pattern 过滤）

---

## File Map

| 文件 | 状态 | 职责 |
|------|------|------|
| `src/agents/mail_bus.py` | 新建 | MailMessage 数据类 + MailBus（asyncio.Lock 保护） |
| `src/agents/sandbox.py` | 新建 | SandboxManager（创建/获取/清理工作区） |
| `src/tools/__init__.py` | 修改 | 导出工具类 |
| `src/tools/filesystem.py` | 新建 | ReadFileTool / WriteFileTool / ListFilesTool |
| `src/tools/shell.py` | 新建 | ShellTool（subprocess + 安全过滤） |
| `src/tools/mail.py` | 新建 | SendMailTool / ReadMailTool |
| `src/agents/worker_pool.py` | 修改 | 增加 `get_worker_for()`、per-task contexts、`refinement_hint` |
| `src/coordinator/iterative_orchestrator.py` | 新建 | IterativeOrchestrator + 数据类 |
| `src/web/api.py` | 修改 | 切换 `/api/tasks` 到 IterativeOrchestrator，扩展响应字段 |
| `tests/test_mail_bus.py` | 新建 | MailBus 单元测试 |
| `tests/test_sandbox.py` | 新建 | SandboxManager 单元测试 |
| `tests/test_filesystem_tools.py` | 新建 | 文件系统工具测试 |
| `tests/test_shell_tool.py` | 新建 | Shell 工具测试 |
| `tests/test_mail_tools.py` | 新建 | 邮件工具测试 |
| `tests/test_iterative_orchestrator.py` | 新建 | 编排器集成测试（Mock LLM） |

---

## Task 1: MailBus

**Files:**
- Create: `src/agents/mail_bus.py`
- Test: `tests/test_mail_bus.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_mail_bus.py
import sys, os, asyncio, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.mail_bus import MailBus, MailMessage


def test_send_and_read_inbox():
    bus = MailBus(task_id="task_test")

    asyncio.run(bus.send("agent_a", "agent_b", "主题", "正文"))
    messages = asyncio.run(bus.read_inbox("agent_b"))

    assert len(messages) == 1
    assert messages[0].from_agent == "agent_a"
    assert messages[0].subject == "主题"
    assert messages[0].body == "正文"
    assert messages[0].read is True


def test_read_inbox_marks_as_read():
    bus = MailBus(task_id="task_test")

    asyncio.run(bus.send("agent_a", "agent_b", "主题", "正文"))
    asyncio.run(bus.read_inbox("agent_b"))   # 标记已读
    messages2 = asyncio.run(bus.read_inbox("agent_b"))  # 再次读取

    assert len(messages2) == 0  # 已读，不返回


def test_get_all_returns_read_and_unread():
    bus = MailBus(task_id="task_test")

    asyncio.run(bus.send("agent_a", "agent_b", "m1", "body1"))
    asyncio.run(bus.read_inbox("agent_b"))  # 标记已读
    asyncio.run(bus.send("agent_a", "agent_b", "m2", "body2"))

    all_msgs = asyncio.run(bus.get_all("agent_b"))
    assert len(all_msgs) == 2


def test_send_returns_id():
    bus = MailBus(task_id="task_test")
    msg_id = asyncio.run(bus.send("a", "b", "s", "body"))
    assert isinstance(msg_id, str)
    assert len(msg_id) > 0


def test_only_own_inbox():
    bus = MailBus(task_id="task_test")
    asyncio.run(bus.send("agent_a", "agent_b", "msg", "body"))
    msgs_c = asyncio.run(bus.read_inbox("agent_c"))
    assert len(msgs_c) == 0


def test_attachments():
    bus = MailBus(task_id="task_test")
    asyncio.run(bus.send("agent_a", "agent_b", "s", "b", attachments=["/path/file.md"]))
    msgs = asyncio.run(bus.read_inbox("agent_b"))
    assert msgs[0].attachments == ["/path/file.md"]
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd /work/memoX && pytest tests/test_mail_bus.py -v
```

期望输出：`ModuleNotFoundError: No module named 'agents.mail_bus'`

- [ ] **Step 3: 实现 MailBus**

```python
# src/agents/mail_bus.py
"""进程内邮件总线 - Agent 间异步通信"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MailMessage:
    """邮件消息"""
    id: str
    from_agent: str
    to_agent: str
    subject: str
    body: str
    attachments: list[str]
    created_at: str
    read: bool = False


class MailBus:
    """进程内邮件总线，每个任务拥有独立实例，任务结束后销毁"""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._lock = asyncio.Lock()
        self._messages: list[MailMessage] = []

    async def send(
        self,
        from_agent: str,
        to_agent: str,
        subject: str,
        body: str,
        attachments: list[str] | None = None,
    ) -> str:
        """发送消息，返回消息 ID"""
        msg = MailMessage(
            id=uuid.uuid4().hex,
            from_agent=from_agent,
            to_agent=to_agent,
            subject=subject,
            body=body,
            attachments=attachments or [],
            created_at=datetime.now().isoformat(),
        )
        async with self._lock:
            self._messages.append(msg)
        return msg.id

    async def read_inbox(self, agent_name: str) -> list[MailMessage]:
        """读取未读消息并标记为已读"""
        async with self._lock:
            unread = [m for m in self._messages if m.to_agent == agent_name and not m.read]
            for m in unread:
                m.read = True
        return unread

    async def mark_read(self, message_id: str) -> None:
        """将指定消息标记为已读"""
        async with self._lock:
            for m in self._messages:
                if m.id == message_id:
                    m.read = True
                    break

    async def get_all(self, agent_name: str) -> list[MailMessage]:
        """获取全部消息（含已读）"""
        async with self._lock:
            return [m for m in self._messages if m.to_agent == agent_name]
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
cd /work/memoX && pytest tests/test_mail_bus.py -v
```

期望输出：`6 passed`

- [ ] **Step 5: Commit**

```bash
cd /work/memoX && git add src/agents/mail_bus.py tests/test_mail_bus.py
git commit -m "feat: add MailBus for inter-agent async messaging"
```

---

## Task 2: SandboxManager

**Files:**
- Create: `src/agents/sandbox.py`
- Test: `tests/test_sandbox.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_sandbox.py
import sys, os, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.sandbox import SandboxManager


def test_create_task_workspace(tmp_path):
    mgr = SandboxManager(base_workspace=tmp_path)
    workspace = mgr.create_task_workspace("task_abc")

    assert workspace.exists()
    assert (workspace / "coordinator").exists()
    assert (workspace / "shared").exists()


def test_get_agent_sandbox_creates_dir(tmp_path):
    mgr = SandboxManager(base_workspace=tmp_path)
    mgr.create_task_workspace("task_abc")
    sandbox = mgr.get_agent_sandbox("task_abc", "code_worker")

    assert sandbox.exists()
    assert sandbox.name == "agent_code_worker"


def test_get_shared_dir(tmp_path):
    mgr = SandboxManager(base_workspace=tmp_path)
    mgr.create_task_workspace("task_abc")
    shared = mgr.get_shared_dir("task_abc")

    assert shared.exists()
    assert shared.name == "shared"


def test_cleanup(tmp_path):
    mgr = SandboxManager(base_workspace=tmp_path)
    mgr.create_task_workspace("task_abc")
    mgr.cleanup("task_abc")

    assert not (tmp_path / "task_abc").exists()


def test_get_agent_sandbox_idempotent(tmp_path):
    mgr = SandboxManager(base_workspace=tmp_path)
    mgr.create_task_workspace("task_abc")
    sandbox1 = mgr.get_agent_sandbox("task_abc", "worker")
    sandbox2 = mgr.get_agent_sandbox("task_abc", "worker")
    assert sandbox1 == sandbox2
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd /work/memoX && pytest tests/test_sandbox.py -v
```

期望输出：`ModuleNotFoundError: No module named 'agents.sandbox'`

- [ ] **Step 3: 实现 SandboxManager**

```python
# src/agents/sandbox.py
"""沙箱目录管理 - 为每个任务和 Agent 创建隔离工作区"""

import shutil
from pathlib import Path


class SandboxManager:
    """为多 Agent 任务创建和管理隔离的文件系统工作区"""

    def __init__(self, base_workspace: str | Path):
        self.base_workspace = Path(base_workspace)

    def create_task_workspace(self, task_id: str) -> Path:
        """创建任务工作区（coordinator/ 和 shared/ 子目录）"""
        workspace = self.base_workspace / task_id
        (workspace / "coordinator").mkdir(parents=True, exist_ok=True)
        (workspace / "shared").mkdir(parents=True, exist_ok=True)
        return workspace

    def get_agent_sandbox(self, task_id: str, agent_name: str) -> Path:
        """获取 Agent 的专属沙箱目录（不存在则自动创建）"""
        sandbox = self.base_workspace / task_id / f"agent_{agent_name}"
        sandbox.mkdir(parents=True, exist_ok=True)
        return sandbox

    def get_shared_dir(self, task_id: str) -> Path:
        """获取任务的共享输出目录"""
        shared = self.base_workspace / task_id / "shared"
        shared.mkdir(parents=True, exist_ok=True)
        return shared

    def cleanup(self, task_id: str) -> None:
        """删除整个任务工作区"""
        workspace = self.base_workspace / task_id
        if workspace.exists():
            shutil.rmtree(workspace)
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
cd /work/memoX && pytest tests/test_sandbox.py -v
```

期望输出：`5 passed`

- [ ] **Step 5: Commit**

```bash
cd /work/memoX && git add src/agents/sandbox.py tests/test_sandbox.py
git commit -m "feat: add SandboxManager for per-task isolated workspaces"
```

---

## Task 3: 文件系统工具

**Files:**
- Create: `src/tools/filesystem.py`
- Test: `tests/test_filesystem_tools.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_filesystem_tools.py
import sys, os, asyncio, pytest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.sandbox import SandboxManager
from tools.filesystem import ReadFileTool, WriteFileTool, ListFilesTool


def make_tools(tmp_path):
    mgr = SandboxManager(tmp_path)
    mgr.create_task_workspace("t1")
    sandbox = mgr.get_agent_sandbox("t1", "worker")
    read_tool = ReadFileTool(sandbox, "t1", mgr)
    write_tool = WriteFileTool(sandbox)
    list_tool = ListFilesTool(sandbox)
    return read_tool, write_tool, list_tool, sandbox


def test_write_and_read(tmp_path):
    read_tool, write_tool, _, sandbox = make_tools(tmp_path)

    asyncio.run(write_tool.execute({"path": "hello.txt", "content": "世界"}))
    result = asyncio.run(read_tool.execute({"path": "hello.txt"}))

    assert result == "世界"


def test_write_outside_sandbox_denied(tmp_path):
    _, write_tool, _, sandbox = make_tools(tmp_path)

    result = asyncio.run(write_tool.execute({"path": "/tmp/evil.txt", "content": "hack"}))
    assert "Error" in result


def test_read_outside_task_workspace_denied(tmp_path):
    read_tool, _, _, _ = make_tools(tmp_path)

    result = asyncio.run(read_tool.execute({"path": "/etc/passwd"}))
    assert "Error" in result


def test_read_nonexistent_file(tmp_path):
    read_tool, _, _, _ = make_tools(tmp_path)

    result = asyncio.run(read_tool.execute({"path": "nonexistent.txt"}))
    assert "Error" in result


def test_list_files(tmp_path):
    read_tool, write_tool, list_tool, _ = make_tools(tmp_path)

    asyncio.run(write_tool.execute({"path": "a.txt", "content": "a"}))
    asyncio.run(write_tool.execute({"path": "b.txt", "content": "b"}))

    result = asyncio.run(list_tool.execute({}))
    assert "a.txt" in result
    assert "b.txt" in result


def test_list_empty_dir(tmp_path):
    _, _, list_tool, _ = make_tools(tmp_path)

    result = asyncio.run(list_tool.execute({}))
    assert "空" in result or result.strip() == ""


def test_read_other_agent_sandbox(tmp_path):
    """Agent 可以读取其他 Agent 沙箱的文件（只读跨沙箱）"""
    mgr = SandboxManager(tmp_path)
    mgr.create_task_workspace("t1")
    sandbox_a = mgr.get_agent_sandbox("t1", "agent_a")
    sandbox_b = mgr.get_agent_sandbox("t1", "agent_b")

    # agent_a 写文件
    write_a = WriteFileTool(sandbox_a)
    asyncio.run(write_a.execute({"path": "design.md", "content": "# 设计文档"}))

    # agent_b 读 agent_a 的文件（绝对路径）
    read_b = ReadFileTool(sandbox_b, "t1", mgr)
    result = asyncio.run(read_b.execute({"path": str(sandbox_a / "design.md")}))
    assert result == "# 设计文档"
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd /work/memoX && pytest tests/test_filesystem_tools.py -v
```

期望输出：`ModuleNotFoundError: No module named 'tools.filesystem'`

- [ ] **Step 3: 实现文件系统工具**

```python
# src/tools/filesystem.py
"""文件系统工具 - 供 Worker Agent 读写沙箱内文件"""

from pathlib import Path
from typing import Any

from agents.base_agent import BaseTool
from agents.sandbox import SandboxManager


class ReadFileTool(BaseTool):
    """读文件工具 - 可读自身沙箱、shared/ 及其他 Agent 沙箱（任务工作区内）"""

    def __init__(self, sandbox_dir: Path, task_id: str, sandbox_mgr: SandboxManager):
        self._sandbox_dir = sandbox_dir
        self._task_workspace = (sandbox_mgr.base_workspace / task_id).resolve()

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "读取文件内容。相对路径相对于自身沙箱；绝对路径必须在任务工作区内（可读其他 Agent 沙箱）。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对或绝对）"},
            },
            "required": ["path"],
        }

    async def execute(self, arguments: dict) -> Any:
        path = Path(arguments["path"])
        if not path.is_absolute():
            path = self._sandbox_dir / path
        path = path.resolve()

        if not str(path).startswith(str(self._task_workspace)):
            return f"Error: 访问被拒绝，路径不在任务工作区内: {path}"
        if not path.exists():
            return f"Error: 文件不存在: {path}"
        if not path.is_file():
            return f"Error: 不是文件: {path}"

        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"(二进制文件，hex 前 256 字节): {path.read_bytes()[:256].hex()}"


class WriteFileTool(BaseTool):
    """写文件工具 - 只能写自身沙箱"""

    def __init__(self, sandbox_dir: Path):
        self._sandbox_dir = sandbox_dir.resolve()

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "写入文件内容。相对路径相对于自身沙箱；绝对路径必须在自身沙箱内。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "文件内容"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, arguments: dict) -> Any:
        path = Path(arguments["path"])
        content = arguments["content"]

        if not path.is_absolute():
            path = self._sandbox_dir / path
        path = path.resolve()

        if not str(path).startswith(str(self._sandbox_dir)):
            return f"Error: 写入被拒绝，只能写入自身沙箱"

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"已写入 {path.name} ({len(content)} 字符)"


class ListFilesTool(BaseTool):
    """列目录工具 - 只能列自身沙箱"""

    def __init__(self, sandbox_dir: Path):
        self._sandbox_dir = sandbox_dir.resolve()

    @property
    def name(self) -> str:
        return "list_files"

    @property
    def description(self) -> str:
        return "列出目录内容（默认为自身沙箱根目录）。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径（默认为工作目录）"},
            },
            "required": [],
        }

    async def execute(self, arguments: dict) -> Any:
        path_str = arguments.get("path", ".")
        path = Path(path_str)

        if not path.is_absolute():
            path = self._sandbox_dir / path
        path = path.resolve()

        if not str(path).startswith(str(self._sandbox_dir)):
            return f"Error: 只能列出自身沙箱目录"
        if not path.exists():
            return f"Error: 目录不存在: {path}"

        items = []
        for item in sorted(path.iterdir()):
            rel = item.relative_to(self._sandbox_dir)
            kind = "DIR " if item.is_dir() else "FILE"
            items.append(f"{kind} {rel}")

        return "\n".join(items) if items else "(空目录)"
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
cd /work/memoX && pytest tests/test_filesystem_tools.py -v
```

期望输出：`7 passed`

- [ ] **Step 5: Commit**

```bash
cd /work/memoX && git add src/tools/filesystem.py tests/test_filesystem_tools.py
git commit -m "feat: add filesystem tools (read/write/list) with sandbox path enforcement"
```

---

## Task 4: Shell 工具

**Files:**
- Create: `src/tools/shell.py`
- Test: `tests/test_shell_tool.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_shell_tool.py
import sys, os, asyncio, pytest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tools.shell import ShellTool


def test_simple_command(tmp_path):
    tool = ShellTool(cwd=tmp_path)
    result = asyncio.run(tool.execute({"command": "echo hello"}))
    assert "hello" in result


def test_cwd_is_sandbox(tmp_path):
    tool = ShellTool(cwd=tmp_path)
    result = asyncio.run(tool.execute({"command": "pwd"}))
    assert str(tmp_path) in result


def test_blocked_rm_rf(tmp_path):
    tool = ShellTool(cwd=tmp_path)
    result = asyncio.run(tool.execute({"command": "rm -rf /"}))
    assert "Error" in result


def test_blocked_curl(tmp_path):
    tool = ShellTool(cwd=tmp_path)
    result = asyncio.run(tool.execute({"command": "curl http://example.com"}))
    assert "Error" in result


def test_blocked_sudo(tmp_path):
    tool = ShellTool(cwd=tmp_path)
    result = asyncio.run(tool.execute({"command": "sudo ls"}))
    assert "Error" in result


def test_timeout_cap(tmp_path):
    tool = ShellTool(cwd=tmp_path)
    # 请求超过 MAX_TIMEOUT=300 的超时，应被截断
    result = asyncio.run(tool.execute({"command": "sleep 1", "timeout": 99999}))
    # 命令能完成（sleep 1 在 300s 内完成）
    assert "Error" not in result or "超时" not in result


def test_stderr_captured(tmp_path):
    tool = ShellTool(cwd=tmp_path)
    result = asyncio.run(tool.execute({"command": "ls /nonexistent_path_xyz"}))
    # ls 失败，但 stderr 被捕获而不是抛出异常
    assert isinstance(result, str)
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd /work/memoX && pytest tests/test_shell_tool.py -v
```

期望输出：`ModuleNotFoundError: No module named 'tools.shell'`

- [ ] **Step 3: 实现 ShellTool**

```python
# src/tools/shell.py
"""Shell 工具 - 在沙箱内运行系统命令"""

import re
import subprocess
from pathlib import Path
from typing import Any

from agents.base_agent import BaseTool

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300

BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",       # 禁止删根目录
    r"curl\s+.*http",       # 禁止外网 HTTP 请求
    r"wget\s+.*http",
    r">\s*/etc/",           # 禁止写系统目录
    r"\bsudo\b",
    r"chmod\s+777",
]


class ShellTool(BaseTool):
    """在 Agent 沙箱内执行 Shell 命令，stdout + stderr 合并返回"""

    def __init__(self, cwd: Path):
        self._cwd = cwd

    @property
    def name(self) -> str:
        return "run_shell"

    @property
    def description(self) -> str:
        return f"在沙箱目录内运行 Shell 命令。stdout + stderr 合并返回。默认超时 {DEFAULT_TIMEOUT}s，最大 {MAX_TIMEOUT}s。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell 命令"},
                "timeout": {
                    "type": "integer",
                    "description": f"超时秒数（最大 {MAX_TIMEOUT}，默认 {DEFAULT_TIMEOUT}）",
                },
            },
            "required": ["command"],
        }

    async def execute(self, arguments: dict) -> Any:
        command = arguments["command"]
        timeout = min(int(arguments.get("timeout", DEFAULT_TIMEOUT)), MAX_TIMEOUT)

        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, command):
                return f"Error: 命令被安全策略阻止（规则: {pattern}）"

        self._cwd.mkdir(parents=True, exist_ok=True)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(self._cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            return output if output.strip() else f"(退出码: {result.returncode})"
        except subprocess.TimeoutExpired:
            return f"Error: 命令超时（{timeout}s）"
        except Exception as e:
            return f"Error: {type(e).__name__}: {str(e)}"
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
cd /work/memoX && pytest tests/test_shell_tool.py -v
```

期望输出：`7 passed`

- [ ] **Step 5: Commit**

```bash
cd /work/memoX && git add src/tools/shell.py tests/test_shell_tool.py
git commit -m "feat: add ShellTool with security pattern filtering and timeout cap"
```

---

## Task 5: 邮件工具

**Files:**
- Create: `src/tools/mail.py`
- Test: `tests/test_mail_tools.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_mail_tools.py
import sys, os, asyncio, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.mail_bus import MailBus
from tools.mail import SendMailTool, ReadMailTool


def make_bus():
    return MailBus(task_id="task_test")


def test_send_and_receive():
    bus = make_bus()
    send_tool = SendMailTool("agent_a", bus)
    read_tool = ReadMailTool("agent_b", bus)

    result = asyncio.run(send_tool.execute({
        "to": "agent_b",
        "subject": "你好",
        "body": "正文内容",
    }))
    assert "已发送" in result

    received = asyncio.run(read_tool.execute({}))
    assert "你好" in received
    assert "正文内容" in received


def test_empty_inbox():
    bus = make_bus()
    read_tool = ReadMailTool("agent_a", bus)

    result = asyncio.run(read_tool.execute({}))
    assert "无未读" in result


def test_read_marks_as_read():
    bus = make_bus()
    send_tool = SendMailTool("agent_a", bus)
    read_tool = ReadMailTool("agent_b", bus)

    asyncio.run(send_tool.execute({"to": "agent_b", "subject": "s", "body": "b"}))
    asyncio.run(read_tool.execute({}))   # 第一次读
    result2 = asyncio.run(read_tool.execute({}))  # 第二次读

    assert "无未读" in result2


def test_attachments_in_mail():
    bus = make_bus()
    send_tool = SendMailTool("agent_a", bus)
    read_tool = ReadMailTool("agent_b", bus)

    asyncio.run(send_tool.execute({
        "to": "agent_b",
        "subject": "带附件",
        "body": "见附件",
        "attachments": ["/sandbox/design.md"],
    }))

    received = asyncio.run(read_tool.execute({}))
    assert "/sandbox/design.md" in received
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd /work/memoX && pytest tests/test_mail_tools.py -v
```

期望输出：`ModuleNotFoundError: No module named 'tools.mail'`

- [ ] **Step 3: 实现邮件工具**

```python
# src/tools/mail.py
"""邮件工具 - 包装 MailBus，供 Worker Agent 调用"""

from typing import Any

from agents.base_agent import BaseTool
from agents.mail_bus import MailBus


class SendMailTool(BaseTool):
    """向其他 Agent 发送邮件"""

    def __init__(self, agent_name: str, mail_bus: MailBus):
        self._agent_name = agent_name
        self._mail_bus = mail_bus

    @property
    def name(self) -> str:
        return "send_mail"

    @property
    def description(self) -> str:
        return "向其他 Agent（或 coordinator）发送邮件。attachments 传沙箱内绝对路径，接收方用 read_file 读取。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "收件人 agent 名称（如 coordinator）"},
                "subject": {"type": "string", "description": "邮件主题"},
                "body": {"type": "string", "description": "邮件正文"},
                "attachments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "文件路径列表（沙箱内绝对路径）",
                },
            },
            "required": ["to", "subject", "body"],
        }

    async def execute(self, arguments: dict) -> Any:
        msg_id = await self._mail_bus.send(
            from_agent=self._agent_name,
            to_agent=arguments["to"],
            subject=arguments["subject"],
            body=arguments["body"],
            attachments=arguments.get("attachments", []),
        )
        return f"邮件已发送，ID: {msg_id}"


class ReadMailTool(BaseTool):
    """读取自己的未读邮件"""

    def __init__(self, agent_name: str, mail_bus: MailBus):
        self._agent_name = agent_name
        self._mail_bus = mail_bus

    @property
    def name(self) -> str:
        return "read_mail"

    @property
    def description(self) -> str:
        return "读取自己的未读邮件，读取后自动标记为已读。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, arguments: dict) -> Any:
        messages = await self._mail_bus.read_inbox(self._agent_name)
        if not messages:
            return "(无未读邮件)"

        parts = []
        for msg in messages:
            part = (
                f"=== 邮件 ID: {msg.id} ===\n"
                f"发件人: {msg.from_agent}\n"
                f"主题: {msg.subject}\n"
                f"时间: {msg.created_at}\n\n"
                f"{msg.body}"
            )
            if msg.attachments:
                part += f"\n\n附件: {', '.join(msg.attachments)}"
            parts.append(part)

        return "\n\n".join(parts)
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
cd /work/memoX && pytest tests/test_mail_tools.py -v
```

期望输出：`4 passed`

- [ ] **Step 5: 更新 tools/__init__.py**

```python
# src/tools/__init__.py
"""Tools 模块"""

from tools.filesystem import ReadFileTool, WriteFileTool, ListFilesTool
from tools.shell import ShellTool
from tools.mail import SendMailTool, ReadMailTool

__all__ = [
    "ReadFileTool",
    "WriteFileTool",
    "ListFilesTool",
    "ShellTool",
    "SendMailTool",
    "ReadMailTool",
]
```

- [ ] **Step 6: Commit**

```bash
cd /work/memoX && git add src/tools/mail.py src/tools/__init__.py tests/test_mail_tools.py
git commit -m "feat: add SendMailTool/ReadMailTool wrapping MailBus"
```

---

## Task 6: WorkerPool / WorkerAgent 改动

**Files:**
- Modify: `src/agents/worker_pool.py`

修复三处：
1. `WorkerAgent` 增加 `refinement_hint` 属性，并在系统提示中使用
2. `WorkerPool` 增加 `get_worker_for(subtask)` 方法
3. `WorkerPool.execute_parallel` 增加 `per_task_contexts` 参数

- [ ] **Step 1: 在 `WorkerAgent.__init__` 末尾添加 `refinement_hint` 属性**

定位到 `src/agents/worker_pool.py` 第 109 行（`self._current_task_id: str | None = None`），在其后添加：

```python
        # 改进指令（由 IterativeOrchestrator 在每轮迭代前注入）
        self.refinement_hint: str | None = None
```

- [ ] **Step 2: 更新 `_build_system_prompt` 在末尾追加改进指令**

定位到 `src/agents/worker_pool.py`，找到 `_build_system_prompt` 方法，将返回的 f-string 末尾修改如下（在 `{skills_info}` 后添加）：

```python
    def _build_system_prompt(self) -> str:
        """构建系统提示"""
        tool_defs = "\n".join([
            f"- **{t['function']['name']}**: {t['function']['description']}"
            for t in self.tools.get_definitions()
        ])

        skills_info = ""
        if self.config.skills:
            skills_info = f"\n\n## 已启用的技能\n{', '.join(self.config.skills)}"

        refinement_section = ""
        if self.refinement_hint:
            refinement_section = f"\n\n## 本轮改进要求（来自 Coordinator）\n{self.refinement_hint}"

        return f"""# Worker Agent: {self.config.name}

你是一个智能助手，负责执行分配给你的任务。

## 可用工具
{tool_defs or '无工具可用'}

## 工作目录
./workspace

## 要求
1. 专注于完成分配的任务
2. 使用工具时提供必要的参数
3. 如果遇到问题，尝试替代方案
4. 完成后返回简洁的结果摘要
{skills_info}{refinement_section}
"""
```

- [ ] **Step 3: 在 `WorkerPool` 类中添加 `get_worker_for` 方法**

定位到 `src/agents/worker_pool.py`，在 `get_available_worker` 方法后添加：

```python
    def get_worker_for(self, subtask) -> "WorkerAgent | None":
        """获取适合执行该子任务的 Worker（当前实现：返回任意空闲 Worker）"""
        return self.get_available_worker()
```

- [ ] **Step 4: 更新 `execute_parallel` 签名和实现**

将 `execute_parallel` 整个方法替换为：

```python
    async def execute_parallel(
        self,
        tasks: list[SubTask],
        context: dict[str, Any] | None = None,
        on_progress: Callable[[str, str], None] | None = None,
        per_task_contexts: dict[str, dict] | None = None,
    ) -> list[tuple[SubTask, str | None, str | None]]:
        """并行执行多个任务。per_task_contexts 优先于 context。"""
        async def run_task(task: SubTask) -> tuple[str, str | None, str | None]:
            task_ctx = per_task_contexts.get(task.id, context) if per_task_contexts else context

            progress_callback = None
            if on_progress:
                def callback(msg: str):
                    on_progress(task.id, msg)
                progress_callback = callback

            result, error = await self.execute_task(task, task_ctx, progress_callback)
            return task.id, result, error

        coroutines = [run_task(task) for task in tasks]
        results = await asyncio.gather(*coroutines, return_exceptions=True)

        output: list[tuple[SubTask, str | None, str | None]] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                output.append((tasks[i], None, str(result)))
            else:
                task_id, result_str, error = result
                output.append((tasks[i], result_str, error))

        return output
```

- [ ] **Step 5: 运行现有测试确保无退化**

```bash
cd /work/memoX && pytest tests/ -v
```

期望输出：所有现有测试通过（test_group_store, test_mail_bus, test_sandbox, test_filesystem_tools, test_shell_tool, test_mail_tools）

- [ ] **Step 6: Commit**

```bash
cd /work/memoX && git add src/agents/worker_pool.py
git commit -m "feat: add refinement_hint, get_worker_for, and per_task_contexts to WorkerPool"
```

---

## Task 7: IterativeOrchestrator

**Files:**
- Create: `src/coordinator/iterative_orchestrator.py`
- Test: `tests/test_iterative_orchestrator.py`

- [ ] **Step 1: 编写失败测试**

```python
# tests/test_iterative_orchestrator.py
import sys, os, asyncio, json, pytest
from pathlib import Path
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.mail_bus import MailBus
from agents.sandbox import SandboxManager
from coordinator.iterative_orchestrator import IterativeOrchestrator, IterationResult


def make_mock_provider(score=0.9, improvements=None):
    """创建模拟 LLM Provider"""
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=MagicMock(
        content=json.dumps({
            "score": score,
            "passed": score >= 0.8,
            "improvements": improvements or [],
        })
    ))
    return provider


def make_mock_planner(task_id="task_abc"):
    """创建模拟 TaskPlanner"""
    from agents.worker_pool import Task, SubTask
    sub = SubTask(id="sub_001", description="编写代码")
    task = Task(id=task_id, description="测试任务", sub_tasks=[sub])

    planner = MagicMock()
    planner.plan_task = AsyncMock(return_value=(task, MagicMock(value="simple")))
    return planner


def make_mock_worker_pool(result="任务完成"):
    """创建模拟 WorkerPool"""
    pool = MagicMock()

    async def fake_execute_parallel(tasks, context=None, on_progress=None, per_task_contexts=None):
        from agents.worker_pool import SubTask
        return [(t, result, None) for t in tasks]

    pool.execute_parallel = fake_execute_parallel
    pool.get_worker_for = MagicMock(return_value=None)  # 无 Worker 时直接跳过工具绑定
    return pool


def make_mock_rag_engine():
    rag = MagicMock()
    rag.search = AsyncMock(return_value=[])
    return rag


def test_run_returns_iteration_result(tmp_path):
    provider = make_mock_provider(score=0.9)
    planner = make_mock_planner()
    pool = make_mock_worker_pool()
    rag = make_mock_rag_engine()

    orchestrator = IterativeOrchestrator(
        planner=planner,
        worker_pool=pool,
        provider=provider,
        rag_engine=rag,
        model="test-model",
        base_workspace=tmp_path,
    )

    result = asyncio.run(orchestrator.run("编写一个 Python 函数"))

    assert isinstance(result, IterationResult)
    assert result.final_score >= 0.8
    assert len(result.iterations) >= 1
    assert result.task_id == "task_abc"


def test_run_stops_when_score_meets_threshold(tmp_path):
    provider = make_mock_provider(score=0.85)
    planner = make_mock_planner()
    pool = make_mock_worker_pool()
    rag = make_mock_rag_engine()

    orchestrator = IterativeOrchestrator(
        planner=planner,
        worker_pool=pool,
        provider=provider,
        rag_engine=rag,
        model="test-model",
        base_workspace=tmp_path,
    )

    result = asyncio.run(orchestrator.run("任务描述"))
    # score=0.85 >= 0.8，应在第一轮后停止
    assert len(result.iterations) == 1


def test_run_iterates_when_score_below_threshold(tmp_path):
    """第一轮 score=0.5（低于阈值），第二轮 score=0.9（通过）"""
    call_count = 0

    provider = MagicMock()

    async def chat_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        score = 0.5 if call_count == 1 else 0.9
        return MagicMock(content=json.dumps({
            "score": score,
            "passed": score >= 0.8,
            "improvements": ["修复问题A"] if score < 0.8 else [],
        }))

    provider.chat = chat_side_effect

    planner = make_mock_planner()
    pool = make_mock_worker_pool()
    rag = make_mock_rag_engine()

    orchestrator = IterativeOrchestrator(
        planner=planner,
        worker_pool=pool,
        provider=provider,
        rag_engine=rag,
        model="test-model",
        base_workspace=tmp_path,
    )

    result = asyncio.run(orchestrator.run("任务描述"))
    assert len(result.iterations) == 2
    assert result.iterations[0].score == 0.5
    assert result.iterations[1].score == 0.9


def test_rag_context_injected(tmp_path):
    provider = make_mock_provider(score=0.9)
    planner = make_mock_planner()
    pool = make_mock_worker_pool()

    rag = MagicMock()
    rag_result = MagicMock()
    rag_result.content = "相关知识"
    rag_result.metadata = {"filename": "doc.md"}
    rag.search = AsyncMock(return_value=[rag_result])

    orchestrator = IterativeOrchestrator(
        planner=planner,
        worker_pool=pool,
        provider=provider,
        rag_engine=rag,
        model="test-model",
        base_workspace=tmp_path,
    )

    asyncio.run(orchestrator.run("任务描述", active_group_ids=["g1"]))

    rag.search.assert_called_once()
    call_kwargs = rag.search.call_args
    assert call_kwargs.kwargs.get("group_ids") == ["g1"] or (
        len(call_kwargs.args) > 1 and call_kwargs.args[1] == ["g1"]
    )
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd /work/memoX && pytest tests/test_iterative_orchestrator.py -v
```

期望输出：`ModuleNotFoundError: No module named 'coordinator.iterative_orchestrator'`

- [ ] **Step 3: 实现 IterativeOrchestrator**

```python
# src/coordinator/iterative_orchestrator.py
"""迭代协作编排器 - 多 Agent 迭代执行主循环"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from agents.base_agent import LLMProvider, ToolRegistry
from agents.mail_bus import MailBus
from agents.sandbox import SandboxManager
from agents.worker_pool import Task, SubTask, TaskStatus, WorkerPool
from coordinator.task_planner import TaskPlanner
from tools.filesystem import ReadFileTool, WriteFileTool, ListFilesTool
from tools.shell import ShellTool
from tools.mail import SendMailTool, ReadMailTool

MAX_ITERATIONS = 50
QUALITY_THRESHOLD = 0.8


@dataclass
class IterationRecord:
    """单次迭代的评估记录"""
    iteration: int
    score: float
    improvements: list[str]


@dataclass
class IterationResult:
    """迭代执行的最终结果"""
    task_id: str
    shared_dir: str
    final_score: float
    iterations: list[IterationRecord]
    result_summary: str = ""


class IterativeOrchestrator:
    """多 Agent 迭代协作编排器"""

    def __init__(
        self,
        planner: TaskPlanner,
        worker_pool: WorkerPool,
        provider: LLMProvider,
        rag_engine: Any,
        model: str,
        temperature: float = 0.3,
        base_workspace: str | Path = "data/workspace",
    ):
        self._planner = planner
        self._worker_pool = worker_pool
        self._provider = provider
        self._rag_engine = rag_engine
        self._model = model
        self._temperature = temperature
        self._sandbox_mgr = SandboxManager(base_workspace)

    async def run(
        self,
        description: str,
        context: dict[str, Any] | None = None,
        active_group_ids: list[str] | None = None,
    ) -> IterationResult:
        """执行迭代协作任务，返回最终结果"""
        ctx = dict(context or {})

        # Step 1: RAG 检索注入
        await self._inject_rag_context(description, ctx, active_group_ids)

        # Step 2: 任务规划
        task, complexity = await self._planner.plan_task(description, ctx)
        logger.info(f"[Orchestrator] 任务 {task.id} 规划完成，复杂度: {complexity.value}，子任务数: {len(task.sub_tasks)}")

        # Step 3: 创建沙箱 + MailBus
        self._sandbox_mgr.create_task_workspace(task.id)
        mail_bus = MailBus(task_id=task.id)

        history: list[IterationRecord] = []
        refinement_instructions = ""
        score = 0.0
        merged_summary = ""

        for iteration in range(MAX_ITERATIONS):
            logger.info(f"[Orchestrator] 任务 {task.id} 第 {iteration + 1} 轮迭代")

            # Step 4: 为 Worker 绑定工具
            self._prepare_workers(task, mail_bus, refinement_instructions)

            # Step 5: 带依赖注入地执行子任务
            await self._execute_with_deps(task, ctx)

            # Step 6: 合并沙箱 → shared/
            merged_summary = self._merge(task)

            # Step 7: 质量评估
            score, improvements = await self._evaluate(description, merged_summary, iteration)
            history.append(IterationRecord(iteration=iteration, score=score, improvements=improvements))
            logger.info(f"[Orchestrator] 第 {iteration + 1} 轮评分: {score:.2f}")

            if score >= QUALITY_THRESHOLD:
                logger.info(f"[Orchestrator] 任务 {task.id} 质量达标，结束迭代")
                break

            refinement_instructions = "\n".join(improvements)

        shared_dir = str(self._sandbox_mgr.get_shared_dir(task.id))

        return IterationResult(
            task_id=task.id,
            shared_dir=shared_dir,
            final_score=score,
            iterations=history,
            result_summary=merged_summary[:2000],
        )

    async def _inject_rag_context(
        self,
        description: str,
        context: dict,
        active_group_ids: list[str] | None,
    ) -> None:
        """将 RAG 检索结果注入 context"""
        if not self._rag_engine:
            return
        try:
            results = await self._rag_engine.search(
                description,
                group_ids=active_group_ids,
                top_k=3,
            )
            if results:
                context["knowledge_context"] = "\n".join(
                    f"[{r.metadata.get('filename', 'doc')}] {r.content[:300]}"
                    for r in results
                )
        except Exception as e:
            logger.warning(f"[Orchestrator] RAG 检索失败: {e}")

    def _prepare_workers(
        self,
        task: Task,
        mail_bus: MailBus,
        refinement_instructions: str,
    ) -> None:
        """为每个子任务的 Worker 动态绑定沙箱工具"""
        for subtask in task.sub_tasks:
            worker = self._worker_pool.get_worker_for(subtask)
            if not worker:
                continue

            sandbox_dir = self._sandbox_mgr.get_agent_sandbox(task.id, worker.config.name)
            registry = ToolRegistry()

            registry.register(ReadFileTool(sandbox_dir, task.id, self._sandbox_mgr))
            registry.register(WriteFileTool(sandbox_dir))
            registry.register(ListFilesTool(sandbox_dir))
            registry.register(ShellTool(cwd=sandbox_dir))
            registry.register(SendMailTool(worker.config.name, mail_bus))
            registry.register(ReadMailTool(worker.config.name, mail_bus))

            worker.tools = registry
            worker.refinement_hint = refinement_instructions or None

    async def _execute_with_deps(self, task: Task, base_context: dict) -> None:
        """按依赖顺序执行子任务，将依赖结果注入后续任务的 context"""
        from datetime import datetime

        completed: dict[str, str] = {}
        pending = list(task.sub_tasks)

        while pending:
            ready = [st for st in pending if all(d in completed for d in st.dependencies)]
            if not ready:
                logger.error(f"[Orchestrator] 循环依赖或死锁，剩余: {[st.id for st in pending]}")
                break

            for st in ready:
                st.status = TaskStatus.RUNNING
                st.started_at = datetime.now().isoformat()

            per_task_ctx = {
                st.id: {
                    **base_context,
                    "dependency_results": {d: completed[d] for d in st.dependencies},
                }
                for st in ready
            }

            results = await self._worker_pool.execute_parallel(
                ready,
                context=base_context,
                per_task_contexts=per_task_ctx,
            )

            for st, result, error in results:
                st.status = TaskStatus.FAILED if error else TaskStatus.COMPLETED
                st.result = result
                st.error = error
                st.completed_at = datetime.now().isoformat()
                completed[st.id] = result or error or ""
                pending.remove(st)

    def _merge(self, task: Task) -> str:
        """读取所有 Agent 沙箱文件，合并到 shared/，返回摘要"""
        task_workspace = self._sandbox_mgr.base_workspace / task.id
        shared_dir = self._sandbox_mgr.get_shared_dir(task.id)

        file_contents: dict[str, str] = {}

        for agent_dir in sorted(task_workspace.iterdir()):
            if agent_dir.name == "shared":
                continue
            if not agent_dir.is_dir():
                continue
            for file_path in sorted(agent_dir.rglob("*")):
                if not file_path.is_file():
                    continue
                rel = file_path.relative_to(task_workspace)
                try:
                    content = file_path.read_text(encoding="utf-8")
                    file_contents[str(rel)] = content
                    dest = shared_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(content, encoding="utf-8")
                except Exception:
                    pass

        if file_contents:
            parts = [f"=== {path} ===\n{content[:500]}" for path, content in file_contents.items()]
            return "\n\n".join(parts)

        # 无文件输出，回退到子任务文本结果
        parts = []
        for st in task.sub_tasks:
            if st.result:
                parts.append(f"[{st.description[:50]}]\n{st.result}")
        return "\n\n".join(parts) if parts else "(无输出)"

    async def _evaluate(
        self,
        description: str,
        merged_summary: str,
        iteration: int,
    ) -> tuple[float, list[str]]:
        """调用 LLM 对当前输出质量评分，返回 (score, improvements)"""
        prompt = f"""你是 Coordinator，评估以下任务的完成质量。

原始需求：{description}
当前输出摘要（shared/ 目录内容）：
{merged_summary[:2000]}
迭代轮次：{iteration + 1} / {MAX_ITERATIONS}

请返回 JSON：
{{
  "score": 0.0-1.0,
  "passed": true/false,
  "improvements": ["具体改进点1", "改进点2"]
}}

评分标准：
- 0.0-0.4：严重缺失，主要功能未实现
- 0.4-0.7：基本完成，但有明显不足
- 0.7-0.8：大体满足需求，有少量问题
- 0.8-1.0：高质量完成，可以接受"""

        messages = [
            {"role": "system", "content": "你是质量评估专家。只返回 JSON，不要其他内容。"},
            {"role": "user", "content": prompt},
        ]

        try:
            response = await self._provider.chat(
                messages=messages,
                model=self._model,
                temperature=0.1,
                max_tokens=500,
            )
            content = response.content or "{}"
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(content)
            return float(data.get("score", 0.5)), data.get("improvements", [])
        except Exception as e:
            logger.warning(f"[Orchestrator] 质量评估失败: {e}，默认 score=0.5")
            return 0.5, []
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
cd /work/memoX && pytest tests/test_iterative_orchestrator.py -v
```

期望输出：`4 passed`

- [ ] **Step 5: 运行全量测试**

```bash
cd /work/memoX && pytest tests/ -v
```

期望输出：全部通过（无失败）

- [ ] **Step 6: Commit**

```bash
cd /work/memoX && git add src/coordinator/iterative_orchestrator.py tests/test_iterative_orchestrator.py
git commit -m "feat: add IterativeOrchestrator with dependency injection and quality evaluation loop"
```

---

## Task 8: API 集成

**Files:**
- Modify: `src/web/api.py`

将 `/api/tasks` 端点切换到 `IterativeOrchestrator`，扩展响应字段（`shared_dir`、`final_score`、`iterations`）。

- [ ] **Step 1: 在 `api.py` 顶部 import 区添加 IterativeOrchestrator**

找到第 41 行（`from coordinator.task_planner import TaskPlanner, init_task_planner`），在其后添加：

```python
from coordinator.iterative_orchestrator import IterativeOrchestrator, IterationResult
```

- [ ] **Step 2: 在全局变量区添加 `_orchestrator`**

找到第 65 行（`_task_planner: TaskPlanner | None = None`），在其后添加：

```python
_orchestrator: IterativeOrchestrator | None = None
```

- [ ] **Step 3: 在 `startup()` 的 `if coordinator_provider_config:` 块内，紧接 `_task_planner = init_task_planner(...)` 之后创建 Orchestrator**

找到以下代码块（约第 266-272 行）：

```python
        _task_planner = init_task_planner(
            coordinator_provider,
            worker_pool,
            _config.coordinator.model,
        )
```

在其后立即追加（仍在 `if coordinator_provider_config:` 缩进内）：

```python
        # 初始化迭代编排器
        global _orchestrator
        _orchestrator = IterativeOrchestrator(
            planner=_task_planner,
            worker_pool=worker_pool,
            provider=coordinator_provider,
            rag_engine=_rag_engine,
            model=_config.coordinator.model,
            temperature=_config.coordinator.temperature,
            base_workspace=str(Path(_config.knowledge_base.persist_directory).parent / "workspace"),
        )
```

- [ ] **Step 4: 替换 `create_task` 端点实现**

将整个 `create_task` 函数替换为：

```python
@app.post("/api/tasks")
async def create_task(request: TaskRequest) -> dict:
    """创建并执行任务（迭代编排器）"""
    if not _orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")

    result = await _orchestrator.run(
        description=request.description,
        context=request.context or {},
        active_group_ids=request.active_group_ids,
    )

    suggestions = []
    if request.generate_suggestions and _task_planner:
        from agents.worker_pool import Task
        placeholder_task = Task(
            id=result.task_id,
            description=request.description,
        )
        suggestions = await _task_planner.generate_optimization_suggestions(
            placeholder_task,
            result.result_summary,
            request.context or {},
        )

    return {
        "task_id": result.task_id,
        "result": result.result_summary,
        "shared_dir": result.shared_dir,
        "final_score": result.final_score,
        "iterations": [
            {
                "iteration": r.iteration,
                "score": r.score,
                "improvements": r.improvements,
            }
            for r in result.iterations
        ],
        "suggestions": [
            {
                "type": s.type,
                "title": s.title,
                "description": s.description,
                "confidence": s.confidence,
                "code_snippet": s.code_snippet,
                "priority": s.priority,
            }
            for s in suggestions
        ],
    }
```

- [ ] **Step 5: 确认 `list_tasks` 和 `get_task` 端点无需修改**

`/api/tasks` GET 和 `/api/tasks/{task_id}` GET 仍通过 `_task_planner` 读取任务，`IterativeOrchestrator` 内部调用 `_planner.plan_task()` 会将任务存入 `_task_planner._tasks`，因此这两个端点无需修改。

- [ ] **Step 6: 启动服务器验证语法无误**

```bash
cd /work/memoX && python -c "import sys; sys.path.insert(0, 'src'); from web.api import app; print('OK')"
```

期望输出：`OK`（无 ImportError）

- [ ] **Step 7: 运行全量测试**

```bash
cd /work/memoX && pytest tests/ -v
```

期望输出：全部通过

- [ ] **Step 8: Commit**

```bash
cd /work/memoX && git add src/web/api.py
git commit -m "feat: switch /api/tasks to IterativeOrchestrator with iteration history in response"
```

---

## 验证清单

完成所有任务后，执行以下验证：

```bash
# 1. 所有单元测试通过
cd /work/memoX && pytest tests/ -v

# 2. 服务启动无报错（Ctrl+C 退出）
python -m src.main &
sleep 3
curl -s http://localhost:8080/api/health | python -m json.tool
kill %1
```

期望 `/api/health` 返回：
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "documents": 0,
  "workers": ...
}
```
