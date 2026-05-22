"""Shell 工具 - 在沙箱内运行系统命令"""

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from src.agents.base_agent import BaseTool

DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300

ALLOWED_COMMANDS = [
    "ls",
    "cat",
    "grep",
    "pwd",
    "echo",
    "python",
    "uv",
    "pytest",
    "ruff",
]
SHELL_CONTROL_PATTERN = re.compile(r"[|;&<>`$()]")


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

        if SHELL_CONTROL_PATTERN.search(command):
            return "Error: 不支持 shell 控制符、管道、重定向或命令替换"

        try:
            argv = shlex.split(command)
        except ValueError as e:
            return f"Error: 命令解析失败: {e}"

        if not argv:
            return "Error: 命令不能为空"

        base_cmd = argv[0]
        if base_cmd not in ALLOWED_COMMANDS:
            return f"Error: 命令不在白名单内，不允许执行: {base_cmd}。允许的命令: {', '.join(ALLOWED_COMMANDS)}"

        self._cwd.mkdir(parents=True, exist_ok=True)

        try:
            result = subprocess.run(
                argv,
                shell=False,
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
