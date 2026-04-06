# tests/test_integration_multiagent.py
import sys, os, asyncio, json, pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.mail_bus import MailBus
from agents.sandbox import SandboxManager
from tools.mail import SendMailTool, ReadMailTool


def test_mailbus_communication(tmp_path):
    """Worker A 通过 SendMailTool 发消息，Worker B 通过 ReadMailTool 收到"""
    sandbox_mgr = SandboxManager(tmp_path)
    task_id = "task_comm"
    sandbox_mgr.create_task_workspace(task_id)
    mail_bus = MailBus(task_id=task_id)

    send_tool = SendMailTool("worker_a", mail_bus)
    read_tool = ReadMailTool("worker_b", mail_bus)

    # Worker A 发送
    send_result = asyncio.run(send_tool.execute({
        "to": "worker_b",
        "subject": "协作通知",
        "body": "文件已就绪，请处理",
    }))
    assert "已发送" in send_result

    # Worker B 读取
    read_result = asyncio.run(read_tool.execute({}))
    assert "协作通知" in read_result
    assert "文件已就绪，请处理" in read_result

    # 再次读取：已读消息不再返回
    read_result2 = asyncio.run(read_tool.execute({}))
    assert "(无未读邮件)" in read_result2

    # MailBus 层面验证
    all_msgs = asyncio.run(mail_bus.get_all("worker_b"))
    assert len(all_msgs) == 1
    assert all_msgs[0].from_agent == "worker_a"
    assert all_msgs[0].read is True


def test_file_collaboration(tmp_path):
    """Worker A 写文件到沙箱，_merge() 后 shared/ 中存在该文件"""
    from coordinator.iterative_orchestrator import IterativeOrchestrator
    from agents.worker_pool import Task, SubTask
    from tools.filesystem import WriteFileTool

    sandbox_mgr = SandboxManager(tmp_path)
    task_id = "task_file"
    sandbox_mgr.create_task_workspace(task_id)

    # Worker A 的沙箱
    sandbox_a = sandbox_mgr.get_agent_sandbox(task_id, "worker_a")
    write_tool = WriteFileTool(sandbox_a)

    # Worker A 写文件
    result = asyncio.run(write_tool.execute({
        "path": "output.txt",
        "content": "Worker A 的输出内容",
    }))
    assert "已写入" in result

    # 构造 Task 触发 _merge()
    task = Task(id=task_id, description="test", sub_tasks=[])
    orchestrator = IterativeOrchestrator(
        planner=MagicMock(),
        worker_pool=MagicMock(),
        provider=MagicMock(),
        rag_engine=None,
        model="fake",
        base_workspace=tmp_path,
    )
    summary = orchestrator._merge(task)

    # 验证 shared/ 目录包含该文件
    shared_dir = sandbox_mgr.get_shared_dir(task_id)
    merged_file = shared_dir / "agent_worker_a" / "output.txt"
    assert merged_file.exists(), f"合并后文件不存在: {merged_file}"
    assert merged_file.read_text() == "Worker A 的输出内容"
    assert "output.txt" in summary


def test_dependency_injection(tmp_path):
    """sub_b 依赖 sub_a，sub_a 的结果自动注入 sub_b 的 context["dependency_results"]"""
    from coordinator.iterative_orchestrator import IterativeOrchestrator
    from agents.worker_pool import Task, SubTask

    sub_a = SubTask(id="sub_a", description="任务A")
    sub_b = SubTask(id="sub_b", description="任务B", dependencies=["sub_a"])
    task = Task(id="task_dep", description="依赖测试", sub_tasks=[sub_a, sub_b])

    captured_contexts: dict[str, dict] = {}

    async def fake_execute_parallel(tasks, context=None, on_progress=None, per_task_contexts=None):
        for t in tasks:
            ctx = per_task_contexts.get(t.id, {}) if per_task_contexts else {}
            captured_contexts[t.id] = ctx
        return [(t, f"结果_{t.id}", None) for t in tasks]

    mock_pool = MagicMock()
    mock_pool.execute_parallel = fake_execute_parallel
    mock_pool.get_worker_for = MagicMock(return_value=None)

    sandbox_mgr = SandboxManager(tmp_path)
    sandbox_mgr.create_task_workspace("task_dep")

    orchestrator = IterativeOrchestrator(
        planner=MagicMock(),
        worker_pool=mock_pool,
        provider=MagicMock(),
        rag_engine=None,
        model="fake",
        base_workspace=tmp_path,
    )
    orchestrator._sandbox_mgr = sandbox_mgr

    asyncio.run(orchestrator._execute_with_deps(task, {}))

    # sub_a 无依赖，context 中无 dependency_results 或为空
    assert captured_contexts["sub_a"].get("dependency_results", {}) == {}

    # sub_b 依赖 sub_a，结果已注入
    assert "dependency_results" in captured_contexts["sub_b"]
    assert captured_contexts["sub_b"]["dependency_results"]["sub_a"] == "结果_sub_a"


def test_merge_collects_all_outputs(tmp_path):
    """两个 Agent 各写不同文件，_merge() 后 shared/ 包含全部文件"""
    from coordinator.iterative_orchestrator import IterativeOrchestrator
    from agents.worker_pool import Task
    from tools.filesystem import WriteFileTool

    sandbox_mgr = SandboxManager(tmp_path)
    task_id = "task_merge"
    sandbox_mgr.create_task_workspace(task_id)

    # Worker A 写 a.txt
    sandbox_a = sandbox_mgr.get_agent_sandbox(task_id, "worker_a")
    asyncio.run(WriteFileTool(sandbox_a).execute({"path": "a.txt", "content": "来自 A"}))

    # Worker B 写 b.txt
    sandbox_b = sandbox_mgr.get_agent_sandbox(task_id, "worker_b")
    asyncio.run(WriteFileTool(sandbox_b).execute({"path": "b.txt", "content": "来自 B"}))

    task = Task(id=task_id, description="test", sub_tasks=[])
    orchestrator = IterativeOrchestrator(
        planner=MagicMock(),
        worker_pool=MagicMock(),
        provider=MagicMock(),
        rag_engine=None,
        model="fake",
        base_workspace=tmp_path,
    )

    orchestrator._merge(task)

    shared = sandbox_mgr.get_shared_dir(task_id)
    assert (shared / "agent_worker_a" / "a.txt").exists()
    assert (shared / "agent_worker_b" / "b.txt").exists()
    assert (shared / "agent_worker_a" / "a.txt").read_text() == "来自 A"
    assert (shared / "agent_worker_b" / "b.txt").read_text() == "来自 B"
