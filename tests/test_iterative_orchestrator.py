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
