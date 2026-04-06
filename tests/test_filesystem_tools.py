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
