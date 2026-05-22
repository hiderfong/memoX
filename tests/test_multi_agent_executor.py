"""MultiAgentExecutor 单元测试"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agents.mail_bus import MailBus
from agents.worker_pool import SubTask, Task, TaskStatus
from coordinator.multi_agent_executor import MultiAgentExecutor, ResultAggregator, SubTaskResult


class FakeWorker:
    def __init__(self, name: str = "worker"):
        self.config = SimpleNamespace(name=name, tools=[], skills=[])
        self.tools = None


class FakeWorkerPool:
    def __init__(self):
        self.worker = FakeWorker()
        self._workers = {self.worker.config.name: self.worker}
        self.contexts: dict[str, dict] = {}

    def get_worker_for(self, subtask):
        return self.worker

    async def execute_task(self, subtask, context=None, on_progress=None):
        subtask.assigned_agent = self.worker.config.name
        self.contexts[subtask.id] = context or {}
        return f"result_{subtask.id}", None


class FakeAggregator:
    def __init__(self):
        self.results: list[SubTaskResult] = []

    async def aggregate(self, results, original_description, task_context=None):
        self.results = results
        return "aggregated", 0.95


def test_result_aggregator_uses_chat_provider():
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=MagicMock(content="聚合后的完整结果，内容足够长用于置信度判断。" * 2))
    aggregator = ResultAggregator(provider, model="mock-model")

    content, confidence = asyncio.run(
        aggregator.aggregate(
            [SubTaskResult(subtask_id="s1", worker_name="w1", content="worker output")],
            "原始任务",
        )
    )

    provider.chat.assert_awaited_once()
    assert "聚合后的完整结果" in content
    assert confidence > 0.5


def test_parallel_executor_runs_dependency_batches(tmp_path):
    pool = FakeWorkerPool()
    mail_bus = MailBus(task_id="task_parallel")
    aggregator = FakeAggregator()
    executor = MultiAgentExecutor(
        worker_pool=pool,
        provider=MagicMock(),
        mail_bus=mail_bus,
        model="mock-model",
        base_workspace=tmp_path,
        result_aggregator=aggregator,
    )

    sub_a = SubTask(id="a", description="first")
    sub_b = SubTask(id="b", description="second", dependencies=["a"])
    task = Task(id="task_parallel", description="parallel task", sub_tasks=[sub_a, sub_b])

    result = asyncio.run(executor.execute_parallel(task, "parallel task", {"seed": "ctx"}))

    assert result.aggregated_content == "aggregated"
    assert result.final_score == 0.95
    assert [r.subtask_id for r in aggregator.results] == ["a", "b"]
    assert pool.contexts["a"]["dependency_results"] == {}
    assert pool.contexts["b"]["dependency_results"] == {"a": "result_a"}
    assert sub_a.status == TaskStatus.COMPLETED
    assert sub_b.status == TaskStatus.COMPLETED

    inter_messages = asyncio.run(mail_bus.get_inter_history())
    assert len(inter_messages) == 2
    assert pool.worker.tools is not None
    assert set(pool.worker.tools.list_tools()) == {
        "read_file",
        "write_file",
        "list_files",
        "run_shell",
        "send_mail",
        "read_mail",
        "broadcast_message",
        "read_broadcasts",
        "web_search",
        "web_fetch",
        "database_query",
        "github_create_issue",
        "github_search",
        "playwright_crawler",
    }


def test_parallel_executor_marks_dependency_deadlock_failed(tmp_path):
    pool = FakeWorkerPool()
    aggregator = FakeAggregator()
    executor = MultiAgentExecutor(
        worker_pool=pool,
        provider=MagicMock(),
        mail_bus=MailBus(task_id="task_deadlock"),
        base_workspace=tmp_path,
        result_aggregator=aggregator,
    )

    sub_a = SubTask(id="a", description="blocked", dependencies=["missing"])
    task = Task(id="task_deadlock", description="deadlock", sub_tasks=[sub_a])

    result = asyncio.run(executor.execute_parallel(task, "deadlock"))

    assert result.subtask_results[0].success is False
    assert "dependencies" in result.subtask_results[0].error
    assert sub_a.status == TaskStatus.FAILED
