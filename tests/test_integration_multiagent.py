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
    orchestrator._sandbox_mgr = sandbox_mgr
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
    orchestrator._sandbox_mgr = sandbox_mgr

    orchestrator._merge(task)

    shared = sandbox_mgr.get_shared_dir(task_id)
    assert (shared / "agent_worker_a" / "a.txt").exists()
    assert (shared / "agent_worker_b" / "b.txt").exists()
    assert (shared / "agent_worker_a" / "a.txt").read_text() == "来自 A"
    assert (shared / "agent_worker_b" / "b.txt").read_text() == "来自 B"


def test_refinement_hint_injected(tmp_path):
    """第一轮 score=0.5，第二轮开始前 Worker 的 refinement_hint 包含改进指令"""
    from coordinator.iterative_orchestrator import IterativeOrchestrator
    from agents.worker_pool import Task, SubTask, WorkerAgent, WorkerConfig, WorkerPool

    call_count = 0

    async def chat_side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        score = 0.5 if call_count == 1 else 0.9
        improvements = ["修复问题A"] if score < 0.8 else []
        return MagicMock(
            content=json.dumps({"score": score, "passed": score >= 0.8, "improvements": improvements}),
            has_tool_calls=False,
            tool_calls=[],
        )

    provider = MagicMock()
    provider.chat = chat_side_effect

    # 真实 WorkerAgent，Mock LLM
    worker_provider = MagicMock()
    worker_provider.chat = AsyncMock(return_value=MagicMock(
        content="完成", has_tool_calls=False, tool_calls=[],
    ))
    config = WorkerConfig(name="worker_x", provider_type="openai", api_key="fake", model="fake")
    worker = WorkerAgent(config=config, provider=worker_provider)

    pool = WorkerPool()
    pool.register_worker(worker)

    sub = SubTask(id="sub_001", description="执行任务")
    task = Task(id="task_hint", description="测试", sub_tasks=[sub])

    mock_planner = MagicMock()
    mock_planner.plan_task = AsyncMock(return_value=(task, MagicMock(value="simple")))

    # Capture refinement_hint values during _prepare_workers calls
    captured_hints = []
    original_prepare = None

    orchestrator = IterativeOrchestrator(
        planner=mock_planner,
        worker_pool=pool,
        provider=provider,
        rag_engine=None,
        model="fake",
        base_workspace=tmp_path,
    )

    original_prepare = orchestrator._prepare_workers

    def capturing_prepare_workers(task_, mail_bus_, hint):
        captured_hints.append(hint)
        original_prepare(task_, mail_bus_, hint)

    orchestrator._prepare_workers = capturing_prepare_workers

    result = asyncio.run(orchestrator.run("测试任务"))

    assert len(result.iterations) == 2
    assert result.iterations[0].score == 0.5
    assert "修复问题A" in result.iterations[0].improvements
    assert result.iterations[1].score == 0.9
    assert result.final_score == 0.9
    # Verify refinement_hint was injected into worker before iteration 2
    assert len(captured_hints) == 2
    assert captured_hints[0] == ""  # First iteration: no hint
    assert "修复问题A" in captured_hints[1]  # Second iteration: hint from previous improvements


def test_worker_tools_bound_per_iteration(tmp_path):
    """_prepare_workers() 为每个 Worker 绑定全部 6 个工具"""
    from coordinator.iterative_orchestrator import IterativeOrchestrator
    from agents.worker_pool import Task, SubTask, WorkerAgent, WorkerConfig, WorkerPool
    from agents.mail_bus import MailBus

    config = WorkerConfig(name="worker_tools", provider_type="openai", api_key="fake", model="fake")
    worker = WorkerAgent(config=config, provider=MagicMock())
    pool = WorkerPool()
    pool.register_worker(worker)

    sub = SubTask(id="sub_t1", description="test")
    task = Task(id="task_tools", description="test", sub_tasks=[sub])

    sandbox_mgr = SandboxManager(tmp_path)
    sandbox_mgr.create_task_workspace("task_tools")
    mail_bus = MailBus(task_id="task_tools")

    orchestrator = IterativeOrchestrator(
        planner=MagicMock(),
        worker_pool=pool,
        provider=MagicMock(),
        rag_engine=None,
        model="fake",
        base_workspace=tmp_path,
    )
    orchestrator._sandbox_mgr = sandbox_mgr

    orchestrator._prepare_workers(task, mail_bus, "")

    tools = worker.tools.list_tools()
    assert set(tools) == {"read_file", "write_file", "list_files", "run_shell", "send_mail", "read_mail"}
