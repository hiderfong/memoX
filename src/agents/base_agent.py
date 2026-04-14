"""基础 Agent - 支持多种 LLM Provider"""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx


@dataclass
class LLMResponse:
    """LLM 响应"""
    content: str | None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)
    
    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    arguments: dict[str, Any]
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class ToolResult:
    """工具执行结果"""
    tool_call_id: str
    name: str
    result: Any
    error: str | None = None


class BaseTool(ABC):
    """工具基类"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述"""
        pass
    
    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """输入模式"""
        pass
    
    @abstractmethod
    async def execute(self, arguments: dict) -> Any:
        """执行工具"""
        pass


class ToolRegistry:
    """工具注册表"""
    
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
    
    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
    
    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
    
    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)
    
    def list_tools(self) -> list[str]:
        return list(self._tools.keys())
    
    def get_definitions(self) -> list[dict]:
        """获取工具定义（OpenAI 格式，用于系统提示展示）"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in self._tools.values()
        ]

    def get_anthropic_definitions(self) -> list[dict]:
        """获取工具定义（Anthropic 格式，用于 API 调用）"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]
    
    async def execute(self, name: str, arguments: dict) -> Any:
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        return await tool.execute(arguments)


class LLMProvider(ABC):
    """LLM Provider 基类"""
    
    @abstractmethod
    async def chat(self, messages: list[dict], model: str, **kwargs) -> LLMResponse:
        """发送聊天请求"""
        pass
    
    @abstractmethod
    async def chat_stream(
        self, 
        messages: list[dict], 
        model: str, 
        on_chunk: Callable[[str], None] | None = None,
        **kwargs
    ) -> LLMResponse:
        """流式聊天"""
        pass


class AnthropicProvider(LLMProvider):
    """Anthropic Claude Provider"""
    
    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
    
    async def chat(self, messages: list[dict], model: str, **kwargs) -> LLMResponse:
        """发送聊天请求"""
        # 转换消息格式
        system_messages = [m for m in messages if m["role"] == "system"]
        chat_messages = [m for m in messages if m["role"] != "system"]
        
        payload: dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
            **kwargs,
        }
        
        if system_messages:
            payload["system"] = "\n\n".join(m["content"] for m in system_messages)
        
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/messages",
                headers=headers,
                json=payload,
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            
            return LLMResponse(
                content=data.get("content", [{}])[0].get("text") if data.get("content") else None,
                reasoning_content=data.get("thinking", {}).get("thinking") if "thinking" in data else None,
                finish_reason=data.get("stop_reason", "stop"),
                usage={
                    "input_tokens": data.get("usage", {}).get("input_tokens", 0),
                    "output_tokens": data.get("usage", {}).get("output_tokens", 0),
                },
            )
    
    async def chat_stream(
        self, 
        messages: list[dict], 
        model: str, 
        on_chunk: Callable[[str], None] | None = None,
        **kwargs
    ) -> LLMResponse:
        """流式聊天"""
        system_messages = [m for m in messages if m["role"] == "system"]
        chat_messages = [m for m in messages if m["role"] != "system"]
        
        payload: dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
            "stream": True,
            **kwargs,
        }
        
        if system_messages:
            payload["system"] = "\n\n".join(m["content"] for m in system_messages)
        
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        
        content_parts: list[str] = []
        
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/messages",
                headers=headers,
                json=payload,
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    
                    try:
                        data = json.loads(data_str)
                        if data.get("type") == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                content_parts.append(text)
                                if on_chunk:
                                    on_chunk(text)
                    except json.JSONDecodeError:
                        continue
        
        return LLMResponse(
            content="".join(content_parts),
            finish_reason="stop",
        )


class OpenAIProvider(LLMProvider):
    """OpenAI Provider"""
    
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", headers: dict = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.extra_headers = headers or {}
    
    async def chat(self, messages: list[dict], model: str, **kwargs) -> LLMResponse:
        """发送聊天请求"""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **kwargs,
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
            **self.extra_headers,
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            
            choice = data["choices"][0]
            message = choice["message"]
            
            tool_calls = []
            if "tool_calls" in message:
                for tc in message["tool_calls"]:
                    tool_calls.append(ToolCall(
                        id=tc["id"],
                        name=tc["function"]["name"],
                        arguments=json.loads(tc["function"]["arguments"]),
                    ))
            
            return LLMResponse(
                content=message.get("content"),
                tool_calls=tool_calls,
                finish_reason=choice.get("finish_reason", "stop"),
                usage=data.get("usage", {}),
            )
    
    async def chat_stream(
        self, 
        messages: list[dict], 
        model: str, 
        on_chunk: Callable[[str], None] | None = None,
        **kwargs
    ) -> LLMResponse:
        """流式聊天"""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            **kwargs,
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
            **self.extra_headers,
        }
        
        content_parts: list[str] = []
        
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            content_parts.append(content)
                            if on_chunk:
                                on_chunk(content)
                    except json.JSONDecodeError:
                        continue
        
        return LLMResponse(
            content="".join(content_parts),
            finish_reason="stop",
        )


class MiniMaxProvider(LLMProvider):
    """MiniMax Provider - Anthropic 兼容格式 (用于 Token Plan)"""
    
    def __init__(self, api_key: str, base_url: str = "https://api.minimaxi.com/anthropic/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
    
    async def chat(self, messages: list[dict], model: str, **kwargs) -> LLMResponse:
        """发送聊天请求 (Anthropic 兼容格式)"""
        import httpx
        
        # Anthropic 格式：分离 system 和 messages
        system_message = ""
        chat_messages = []
        
        for msg in messages:
            if msg.get("role") == "system":
                system_message = msg.get("content", "")
            else:
                chat_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })
        
        payload = {
            "model": model,
            "messages": chat_messages,
            **kwargs,
        }
        
        if system_message:
            payload["system"] = system_message
        
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/messages",
                headers=headers,
                json=payload,
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            
            # 解析 content 数组（可能包含 thinking、text 和 tool_use 块）
            content_blocks = data.get("content", [])
            text_content = ""
            tool_calls: list[ToolCall] = []
            for block in content_blocks:
                block_type = block.get("type")
                if block_type == "text":
                    text_content = block.get("text", "")
                elif block_type == "tool_use":
                    raw_input = block.get("input", {})
                    if isinstance(raw_input, str):
                        try:
                            raw_input = json.loads(raw_input)
                        except Exception:
                            raw_input = {}
                    tool_calls.append(ToolCall(
                        id=block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        name=block.get("name", ""),
                        arguments=raw_input,
                    ))

            return LLMResponse(
                content=text_content,
                tool_calls=tool_calls,
                finish_reason=data.get("stop_reason", "stop"),
                usage={
                    "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
                    "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
                },
            )
    
    async def chat_stream(
        self, 
        messages: list[dict], 
        model: str, 
        on_chunk: Callable[[str], None] | None = None,
        **kwargs
    ) -> LLMResponse:
        """流式聊天 (Anthropic 兼容格式)"""
        import httpx
        
        system_message = ""
        chat_messages = []
        
        for msg in messages:
            if msg.get("role") == "system":
                system_message = msg.get("content", "")
            else:
                chat_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })
        
        payload = {
            "model": model,
            "messages": chat_messages,
            "stream": True,
            **kwargs,
        }
        
        if system_message:
            payload["system"] = system_message
        
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        
        content_parts: list[str] = []
        
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers=headers,
                json=payload,
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    
                    try:
                        data = json.loads(data_str)
                        if data.get("type") == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                content_parts.append(text)
                                if on_chunk:
                                    on_chunk(text)
                    except json.JSONDecodeError:
                        continue
        
        return LLMResponse(
            content="".join(content_parts),
            finish_reason="stop",
        )


def create_provider(
    provider_type: str,
    api_key: str,
    base_url: str = "",
    headers: dict | None = None,
    **kwargs,
) -> LLMProvider:
    """创建 LLM Provider

    base_url / headers 会按 provider 类型过滤后转发给具体实现：
    - headers 仅 OpenAIProvider（openai / kimi）支持
    """
    providers = {
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "minimax": MiniMaxProvider,
        "kimi": OpenAIProvider,  # Kimi 使用 OpenAI 兼容 API
        "dashscope": OpenAIProvider,  # 阿里云 DashScope 使用 OpenAI 兼容 API
    }

    ptype = provider_type.lower()
    provider_class = providers.get(ptype)
    if not provider_class:
        raise ValueError(f"Unknown provider: {provider_type}")

    call_kwargs = dict(kwargs)
    if base_url:
        call_kwargs["base_url"] = base_url
    if headers and ptype in ("openai", "kimi"):
        call_kwargs["headers"] = headers
    return provider_class(api_key, **call_kwargs)
