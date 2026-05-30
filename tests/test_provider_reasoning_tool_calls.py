import os
import sys
from copy import deepcopy

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agents.base_agent import (
    BaseTool,
    LLMResponse,
    ToolCall,
    ToolRegistry,
    create_provider,
    get_provider_capabilities,
)
from agents.worker_pool import WorkerAgent, WorkerConfig


class _EchoTool(BaseTool):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "Echo a value"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

    async def execute(self, arguments: dict):
        return f"echo:{arguments['value']}"


class _ReasoningToolProvider:
    preserve_reasoning_content = True

    def __init__(self):
        self.calls = []

    async def chat(self, messages, model, **kwargs):
        self.calls.append(deepcopy(messages))
        if len(self.calls) == 1:
            return LLMResponse(
                content="",
                reasoning_content="tool call reasoning",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="echo_tool",
                        arguments={"value": "ok"},
                    )
                ],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="done", finish_reason="stop")

    async def chat_stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_deepseek_reasoning_content_is_preserved_for_tool_result_roundtrip():
    registry = ToolRegistry()
    registry.register(_EchoTool())
    provider = _ReasoningToolProvider()
    worker = WorkerAgent(
        WorkerConfig(
            name="deepseek_worker",
            provider_type="deepseek",
            api_key="",
            model="deepseek-v4-pro",
        ),
        tools=registry,
        provider=provider,
    )

    result = await worker._run_agent_loop([{"role": "user", "content": "call echo"}])

    assert result == "done"
    second_messages = provider.calls[1]
    assistant_message = next(message for message in second_messages if message["role"] == "assistant")
    assert assistant_message["reasoning_content"] == "tool call reasoning"
    assert assistant_message["tool_calls"][0]["function"]["name"] == "echo_tool"


def test_deepseek_provider_uses_openai_compatible_defaults():
    provider = create_provider("deepseek", "test-key")

    assert provider.base_url == "https://api.deepseek.com"
    assert getattr(provider, "preserve_reasoning_content", False) is True


def test_deepseek_provider_capabilities_are_registered():
    capabilities = get_provider_capabilities("deepseek")

    assert capabilities is not None
    assert capabilities.protocol == "openai_compatible"
    assert capabilities.supports_tool_calls is True
    assert capabilities.supports_streaming is True
    assert capabilities.preserves_reasoning_content is True
    assert "deepseek-v4-pro" in capabilities.well_known_models


def test_qwen_provider_uses_dashscope_openai_compatible_defaults():
    provider = create_provider("dashscope", "test-key")

    assert provider.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert getattr(provider, "preserve_reasoning_content", False) is False


def test_qwen_provider_capabilities_include_qwen37():
    capabilities = get_provider_capabilities("dashscope")

    assert capabilities is not None
    assert capabilities.protocol == "openai_compatible"
    assert capabilities.supports_tool_calls is True
    assert capabilities.supports_streaming is True
    assert "qwen3.7" in capabilities.well_known_models
