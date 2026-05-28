# tests/test_iterative_orchestrator.py
import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from coordinator.iterative_orchestrator import IterationResult, IterativeOrchestrator


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
    from agents.worker_pool import SubTask, Task
    sub = SubTask(id="sub_001", description="编写代码")
    task = Task(id=task_id, description="测试任务", sub_tasks=[sub])

    planner = MagicMock()
    planner.plan_task = AsyncMock(return_value=(task, MagicMock(value="simple")))
    return planner


def make_mock_worker_pool(result="任务完成"):
    """创建模拟 WorkerPool"""
    pool = MagicMock()

    async def fake_execute_parallel(tasks, context=None, on_progress=None, per_task_contexts=None):
        return [(t, result, None) for t in tasks]

    pool.execute_parallel = fake_execute_parallel
    pool.get_worker_for = MagicMock(return_value=None)  # 无 Worker 时直接跳过工具绑定
    return pool


def make_mock_rag_engine():
    rag = MagicMock()
    rag.search = AsyncMock(return_value=[])
    return rag


def test_progress_parser_records_llm_usage():
    event = IterativeOrchestrator._provider_progress_event(
        "sub_001",
        "llm_usage: worker=researcher input=120 output=45 call=2",
        iteration=3,
    )

    assert event == (
        "llm_usage",
        {
            "subtask_id": "sub_001",
            "iteration": 3,
            "worker_id": "researcher",
            "input_tokens": 120,
            "output_tokens": 45,
            "total_tokens": 165,
            "call_count": 2,
        },
    )


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


def test_execute_with_deps_retries_failed_subtask(tmp_path):
    from agents.worker_pool import SubTask, Task, TaskStatus

    sub = SubTask(id="sub_retry", description="会重试的任务")
    task = Task(id="task_retry", description="重试测试", sub_tasks=[sub])
    calls = 0
    updates = []

    async def fake_execute_parallel(tasks, context=None, on_progress=None, per_task_contexts=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [(tasks[0], None, "temporary error")]
        return [(tasks[0], "ok", None)]

    def on_update(task_, event_type, details):
        updates.append((event_type, details))

    pool = MagicMock()
    pool.execute_parallel = fake_execute_parallel
    pool.get_worker_for = MagicMock(return_value=None)

    orchestrator = IterativeOrchestrator(
        planner=MagicMock(),
        worker_pool=pool,
        provider=MagicMock(),
        rag_engine=None,
        model="test-model",
        base_workspace=tmp_path,
        subtask_max_attempts=2,
    )

    asyncio.run(orchestrator._execute_with_deps(task, {}, on_task_update=on_update))

    assert calls == 2
    assert sub.status == TaskStatus.COMPLETED
    assert sub.result == "ok"
    assert sub.attempts == 2
    assert any(event == "subtask_retry_scheduled" for event, _details in updates)


def test_run_resumes_from_checkpoint_without_replanning_completed_subtasks(tmp_path):
    provider = make_mock_provider(score=0.9)
    planner = MagicMock()
    planner.plan_task = AsyncMock(side_effect=AssertionError("should not replan a checkpointed task"))
    executed = []
    captured_contexts = {}

    async def fake_execute_parallel(tasks, context=None, on_progress=None, per_task_contexts=None):
        executed.extend(t.id for t in tasks)
        captured_contexts.update(per_task_contexts or {})
        return [(tasks[0], "续跑完成", None)]

    pool = MagicMock()
    pool.execute_parallel = fake_execute_parallel
    pool.get_worker_for = MagicMock(return_value=None)

    checkpoint = {
        "task_id": "task_resume",
        "description": "恢复任务",
        "status": "running",
        "sub_tasks": [
            {
                "id": "sub_done",
                "description": "已完成调研",
                "status": "completed",
                "result": "调研完成",
                "attempts": 1,
            },
            {
                "id": "sub_todo",
                "description": "继续写作",
                "dependencies": ["sub_done"],
                "status": "pending",
                "attempts": 0,
            },
        ],
    }

    orchestrator = IterativeOrchestrator(
        planner=planner,
        worker_pool=pool,
        provider=provider,
        rag_engine=None,
        model="test-model",
        base_workspace=tmp_path,
    )

    result = asyncio.run(
        orchestrator.run(
            "恢复任务",
            context={"_resume_checkpoint": checkpoint},
            task_id="task_resume",
        )
    )

    assert result.task_id == "task_resume"
    assert executed == ["sub_todo"]
    assert captured_contexts["sub_todo"]["dependency_results"] == {"sub_done": "调研完成"}
    assert "_resume_checkpoint" not in captured_contexts["sub_todo"]
    planner.plan_task.assert_not_called()


def test_rag_context_injected(tmp_path):
    provider = make_mock_provider(score=0.9)
    planner = make_mock_planner()
    pool = make_mock_worker_pool()

    rag = MagicMock()
    rag_result = MagicMock()
    rag_result.content = "相关知识"
    rag_result.metadata = {"filename": "doc.md"}
    rag.search = AsyncMock(return_value=[])
    rag.search_with_graph = AsyncMock(return_value={
        "search_results": [rag_result],
        "graph_result": None,
        "graph_boosted_ids": [],
    })

    orchestrator = IterativeOrchestrator(
        planner=planner,
        worker_pool=pool,
        provider=provider,
        rag_engine=rag,
        model="test-model",
        base_workspace=tmp_path,
    )

    asyncio.run(orchestrator.run("任务描述", active_group_ids=["g1"]))

    rag.search_with_graph.assert_called_once()
    call_kwargs = rag.search_with_graph.call_args
    assert call_kwargs.kwargs.get("group_ids") == ["g1"] or (
        len(call_kwargs.args) > 1 and call_kwargs.args[1] == ["g1"]
    )

    plan_context = planner.plan_task.call_args.args[1]
    assert "相关知识" in plan_context["knowledge_context"]
