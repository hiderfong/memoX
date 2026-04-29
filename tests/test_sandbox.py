import os
import sys

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


def test_create_task_workspace_idempotent(tmp_path):
    """重复创建同一任务工作区不应报错"""
    mgr = SandboxManager(base_workspace=tmp_path)
    ws1 = mgr.create_task_workspace("task_x")
    ws2 = mgr.create_task_workspace("task_x")  # idempotent
    assert ws1 == ws2
    assert (tmp_path / "task_x" / "coordinator").exists()
    assert (tmp_path / "task_x" / "shared").exists()


def test_cleanup_nonexistent(tmp_path):
    """清理不存在的工作区应为静默操作"""
    mgr = SandboxManager(base_workspace=tmp_path)
    mgr.cleanup("nonexistent_task")  # 不应抛出

