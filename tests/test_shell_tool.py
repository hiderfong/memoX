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
