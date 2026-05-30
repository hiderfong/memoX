"""Worker provider retry and fallback tests."""

import asyncio
import os
import sys
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agents.base_agent import LLMResponse  # noqa: E402
from agents.worker_pool import (  # noqa: E402
    ProviderFallbackConfig,
    SubTask,
    Task,
    WorkerAgent,
    WorkerConfig,
    WorkerPool,
)
from coordinator.iterative_orchestrator import IterativeOrchestrator  # noqa: E402
from coordinator.task_jobs import TaskJobRequest, TaskJobRunner  # noqa: E402
from storage.persistence import PersistenceStore  # noqa: E402


class _FailingProvider:
    preserve_reasoning_content = False

    def __init__(self, status_code: int):
        self.status_code = status_code
        self.calls = 0

    async def chat(self, messages, model, **kwargs):
        self.calls += 1
        request = httpx.Request("POST", "https://primary.example.test/chat")
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)

    async def chat_stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


class _SuccessProvider:
    preserve_reasoning_content = False

    def __init__(self):
        self.calls = 0
        self.models: list[str] = []

    async def chat(self, messages, model, **kwargs):
        self.calls += 1
        self.models.append(model)
        return LLMResponse(content="fallback ok", finish_reason="stop")

    async def chat_stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


class _SingleSubtaskPlanner:
    def __init__(self, worker_id: str):
        self.worker_id = worker_id

    async def plan_task(self, description, context):
        task_id = str(context.get("_task_id") or "task_fallback")
        return (
            Task(
                id=task_id,
                description=description,
                sub_tasks=[
                    SubTask(
                        id="sub_1",
                        description=description,
                        assigned_agent=self.worker_id,
                    )
                ],
            ),
            SimpleNamespace(value="simple"),
        )


class _QualityProvider:
    async def chat(self, *args, **kwargs):
        return LLMResponse(
            content='{"score": 1.0, "passed": true, "improvements": []}',
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


async def _wait_until_done(runner: TaskJobRunner) -> None:
    for _ in range(100):
        if not runner.list_running():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("task job did not finish")


@pytest.mark.asyncio
async def test_worker_falls_back_after_retryable_provider_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fallback_provider = _SuccessProvider()
    monkeypatch.setattr("agents.worker_pool.create_provider", lambda *args, **kwargs: fallback_provider)
    primary_provider = _FailingProvider(429)
    worker = WorkerAgent(
        WorkerConfig(
            name="fallback_worker",
            provider_type="deepseek",
            api_key="primary-key",
            model="deepseek-v4-pro",
            fallback_providers=[
                ProviderFallbackConfig(
                    provider_type="dashscope",
                    api_key="fallback-key",
                    model="qwen3.7",
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                )
            ],
            provider_retry_attempts=1,
            provider_retry_backoff_seconds=0,
        ),
        provider=primary_provider,
    )
    progress: list[str] = []

    result = await worker._run_agent_loop([{"role": "user", "content": "hello"}], progress.append)

    assert result == "fallback ok"
    assert primary_provider.calls == 2
    assert fallback_provider.calls == 1
    assert fallback_provider.models == ["qwen3.7"]
    assert any(message.startswith("provider_retry:") for message in progress)
    assert any(message.startswith("provider_fallback:") for message in progress)
    assert any(log["message"] == "LLM provider 调用恢复" for log in worker.get_logs())


@pytest.mark.asyncio
async def test_worker_does_not_fall_back_for_non_retryable_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_provider = _SuccessProvider()
    monkeypatch.setattr("agents.worker_pool.create_provider", lambda *args, **kwargs: fallback_provider)
    primary_provider = _FailingProvider(400)
    worker = WorkerAgent(
        WorkerConfig(
            name="fallback_worker",
            provider_type="deepseek",
            api_key="primary-key",
            model="deepseek-v4-pro",
            fallback_providers=[
                ProviderFallbackConfig(
                    provider_type="dashscope",
                    api_key="fallback-key",
                    model="qwen3.7",
                )
            ],
            provider_retry_attempts=1,
            provider_retry_backoff_seconds=0,
        ),
        provider=primary_provider,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await worker._run_agent_loop([{"role": "user", "content": "hello"}])

    assert primary_provider.calls == 1
    assert fallback_provider.calls == 0


@pytest.mark.asyncio
async def test_background_task_job_completes_after_worker_provider_fallback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_provider = _SuccessProvider()
    monkeypatch.setattr("agents.worker_pool.create_provider", lambda *args, **kwargs: fallback_provider)
    primary_provider = _FailingProvider(500)
    worker = WorkerAgent(
        WorkerConfig(
            name="fallback_worker",
            provider_type="deepseek",
            api_key="primary-key",
            model="deepseek-v4-pro",
            fallback_providers=[
                ProviderFallbackConfig(
                    provider_type="dashscope",
                    api_key="fallback-key",
                    model="qwen3.7",
                )
            ],
            provider_retry_attempts=1,
            provider_retry_backoff_seconds=0,
        ),
        provider=primary_provider,
    )
    pool = WorkerPool()
    pool.register_worker(worker)
    store = PersistenceStore(tmp_path / "jobs.db")
    orchestrator = IterativeOrchestrator(
        planner=_SingleSubtaskPlanner(worker.id),
        worker_pool=pool,
        provider=_QualityProvider(),
        rag_engine=None,
        model="quality-model",
        base_workspace=tmp_path / "workspace",
        max_iterations=1,
    )
    runner = TaskJobRunner(
        orchestrator,
        None,
        store,
        {},
        owner_id="runner_fallback",
    )

    accepted = runner.submit(TaskJobRequest(description="需要 fallback 的后台任务", task_id="task_fallback"))
    await _wait_until_done(runner)

    persisted = store.get_task("task_fallback")
    events = store.list_task_events("task_fallback")

    assert accepted["status"] == "queued"
    assert persisted["status"] == "completed"
    assert "fallback ok" in persisted["result"]
    event_types = [event["event_type"] for event in events]
    assert "provider_retry" in event_types
    assert "provider_fallback" in event_types
    assert event_types[-1] == "completed"
    retry_event = next(event for event in events if event["event_type"] == "provider_retry")
    fallback_event = next(event for event in events if event["event_type"] == "provider_fallback")
    assert retry_event["message"] == "子任务 sub_1 provider 调用失败，正在重试"
    assert retry_event["details"]["provider"] == "deepseek"
    assert retry_event["details"]["model"] == "deepseek-v4-pro"
    assert retry_event["details"]["attempt"] == 1
    assert retry_event["details"]["error"] == "HTTP 500"
    assert fallback_event["message"] == "子任务 sub_1 已切换 fallback provider"
    assert fallback_event["details"]["from_provider"] == "deepseek"
    assert fallback_event["details"]["to_provider"] == "dashscope"
    assert fallback_event["details"]["to_model"] == "qwen3.7"
    assert primary_provider.calls == 2
    assert fallback_provider.calls == 1
    assert any(log["message"] == "LLM provider transient failure" for log in worker.get_logs())
    assert any(log["message"] == "LLM provider 调用恢复" for log in worker.get_logs())
    store.close()
