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
