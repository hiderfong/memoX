"""Worker provider retry and fallback tests."""

import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agents.base_agent import LLMResponse  # noqa: E402
from agents.worker_pool import ProviderFallbackConfig, WorkerAgent, WorkerConfig  # noqa: E402


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
                    provider_type="kimi",
                    api_key="fallback-key",
                    model="kimi-latest",
                    base_url="https://api.kimi.com/coding/v1",
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
    assert fallback_provider.models == ["kimi-latest"]
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
                    provider_type="kimi",
                    api_key="fallback-key",
                    model="kimi-latest",
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
