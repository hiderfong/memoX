import asyncio
import json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.worker_pool import WorkerAgent, WorkerConfig, WorkerPool
from coordinator.task_planner import TaskComplexity, TaskPlanner


def _worker(name: str, skills: list[str]) -> WorkerAgent:
    cfg = WorkerConfig(
        name=name,
        provider_type="mock",
        api_key="fake",
        model="mock-model",
        skills=skills,
        tools=["filesystem"],
        display_name=name.title(),
    )
    return WorkerAgent(cfg, provider=MagicMock())


def test_planner_preserves_worker_assignment_and_acceptance_criteria():
    pool = WorkerPool()
    pool.register_worker(_worker("researcher", ["research"]))
    pool.register_worker(_worker("writer", ["writing"]))

    provider = MagicMock()
    provider.chat = MagicMock()

    async def chat(**kwargs):
        return MagicMock(content=json.dumps({
            "complexity": "sequential",
            "reasoning": "先调研后撰写",
            "sub_tasks": [
                {
                    "description": "收集参考资料",
                    "worker": "researcher",
                    "dependencies": [],
                    "acceptance_criteria": ["列出不少于 3 条来源明确的要点"],
                },
                {
                    "description": "撰写报告",
                    "worker": "writer",
                    "dependencies": ["1"],
                    "acceptance_criteria": ["报告包含摘要和结论"],
                },
            ],
        }))

    provider.chat = chat
    planner = TaskPlanner(provider, pool, model="mock")

    task, complexity = asyncio.run(planner.plan_task("写一份调研报告"))

    assert complexity == TaskComplexity.SEQUENTIAL
    assert task.sub_tasks[0].assigned_agent == "researcher"
    assert task.sub_tasks[0].acceptance_criteria == ["列出不少于 3 条来源明确的要点"]
    assert task.sub_tasks[1].assigned_agent == "writer"
    assert task.sub_tasks[1].dependencies == [task.sub_tasks[0].id]
    assert task.sub_tasks[1].acceptance_criteria == ["报告包含摘要和结论"]


def test_planner_ignores_unknown_worker_assignment():
    pool = WorkerPool()
    pool.register_worker(_worker("writer", ["writing"]))

    async def chat(**kwargs):
        return MagicMock(content=json.dumps({
            "complexity": "simple",
            "sub_tasks": [
                {
                    "description": "写摘要",
                    "worker": "missing_worker",
                    "dependencies": [],
                    "acceptance_criteria": "输出中文摘要",
                }
            ],
        }))

    provider = MagicMock()
    provider.chat = chat
    planner = TaskPlanner(provider, pool, model="mock")

    task, _ = asyncio.run(planner.plan_task("写摘要"))

    assert task.sub_tasks[0].assigned_agent is None
    assert task.sub_tasks[0].acceptance_criteria == ["输出中文摘要"]


def test_planner_uses_requested_task_id_from_internal_context():
    pool = WorkerPool()
    captured_messages = []

    async def chat(**kwargs):
        captured_messages.extend(kwargs["messages"])
        return MagicMock(content=json.dumps({
            "complexity": "simple",
            "sub_tasks": [
                {
                    "description": "写摘要",
                    "worker": None,
                    "dependencies": [],
                    "acceptance_criteria": ["输出中文摘要"],
                }
            ],
        }))

    provider = MagicMock()
    provider.chat = chat
    planner = TaskPlanner(provider, pool, model="mock")

    task, _ = asyncio.run(planner.plan_task("写摘要", {"_task_id": "task_fixed", "visible": "yes"}))

    assert task.id == "task_fixed"
    assert "_task_id" not in captured_messages[-1]["content"]
    assert '"visible": "yes"' in captured_messages[-1]["content"]
