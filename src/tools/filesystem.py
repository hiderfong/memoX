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
            return "Error: 写入被拒绝，只能写入自身沙箱"

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
            return "Error: 只能列出自身沙箱目录"
        if not path.exists():
            return f"Error: 目录不存在: {path}"

        items = []
        for item in sorted(path.iterdir()):
            rel = item.relative_to(self._sandbox_dir)
            kind = "DIR " if item.is_dir() else "FILE"
            items.append(f"{kind} {rel}")

        return "\n".join(items) if items else "(空目录)"
