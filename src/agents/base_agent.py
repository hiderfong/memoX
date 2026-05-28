"""基础 Agent - 支持多种 LLM Provider"""

from __future__ import annotations

import json
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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

    def __init__(self, audit_context: dict[str, Any] | None = None):
        self._tools: dict[str, BaseTool] = {}
        self._audit_context: dict[str, Any] = dict(audit_context or {})

    def set_audit_context(self, context: dict[str, Any] | None) -> None:
        """Set contextual metadata used when auditing tool calls."""
        self._audit_context = dict(context or {})

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
            self._record_tool_audit(
                name,
                arguments,
                status="error",
                duration_ms=0,
                error=f"Unknown tool: {name}",
            )
            raise ValueError(f"Unknown tool: {name}")

        started = time.monotonic()
        try:
            result = await tool.execute(arguments)
        except Exception as exc:
            self._record_tool_audit(
                name,
                arguments,
                status="error",
                duration_ms=int((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        self._record_tool_audit(
            name,
            arguments,
            status=_classify_tool_result(result),
            duration_ms=int((time.monotonic() - started) * 1000),
            result=result,
        )
        return result

    def _record_tool_audit(
        self,
        name: str,
        arguments: dict,
        *,
        status: str,
        duration_ms: int,
        result: Any = None,
        error: str = "",
    ) -> None:
        try:
            store = _get_persistence_store()
            if not store:
                return

            worker_id = str(self._audit_context.get("worker_id") or "")
            store.log_audit_event(
                action="tool_call",
                resource="tool",
                resource_id=name,
                username=str(self._audit_context.get("username") or worker_id),
                user_role=str(self._audit_context.get("user_role") or ("worker" if worker_id else "")),
                details={
                    **self._audit_context,
                    "tool_name": name,
                    "status": status,
                    "duration_ms": duration_ms,
                    "arguments": _summarize_tool_arguments(arguments),
                    "result": _summarize_tool_result(result) if error == "" else None,
                    "error": error,
                },
            )
        except Exception:
            pass


SENSITIVE_ARGUMENT_KEYS = {
    "api_key",
    "authorization",
    "auth",
    "cookie",
    "password",
    "secret",
    "token",
}

REJECTED_RESULT_PREFIXES = (
    "Database query rejected:",
    "Web fetch rejected:",
    "Web search rejected:",
    "Playwright crawler rejected:",
    "GitHub tool rejected:",
    "Shell command rejected:",
)

REJECTED_ERROR_MARKERS = (
    "not allowed",
    "blocked",
    "rejected",
    "不允许",
    "拒绝",
    "禁止",
    "被拒绝",
)


def _get_persistence_store() -> Any:
    try:
        from storage import get_store
    except ImportError:
        from src.storage import get_store

    return get_store()


def _classify_tool_result(result: Any) -> str:
    if isinstance(result, str):
        first_line = result.strip().splitlines()[0] if result.strip() else ""
        if first_line.startswith(REJECTED_RESULT_PREFIXES):
            return "rejected"
        if first_line.startswith("Error:"):
            lowered = first_line.lower()
            if any(marker in lowered for marker in REJECTED_ERROR_MARKERS):
                return "rejected"
            return "error"
    return "success"


def _summarize_connection_string(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<redacted>"

    if parsed.scheme.startswith("sqlite"):
        return f"{parsed.scheme}:///<sqlite-db>"
    if not parsed.scheme:
        return "<redacted>"

    host = parsed.hostname or ""
    try:
        port_number = parsed.port
    except ValueError:
        port_number = None
    port = f":{port_number}" if port_number else ""
    database = parsed.path.strip("/").split("/", 1)[0]
    path = f"/{database}" if database else ""
    return f"{parsed.scheme}://{host}{port}{path}"


def _summarize_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    try:
        port_number = parsed.port
    except ValueError:
        port_number = None
    host = parsed.hostname or ""
    netloc = f"{host}:{port_number}" if port_number else host
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _summarize_sql(value: str) -> dict[str, Any]:
    stripped = value.lstrip()
    verb = stripped.split(None, 1)[0].lower() if stripped else ""
    return {"statement_type": verb, "length": len(value)}


def _safe_preview(value: str, limit: int = 160) -> str:
    single_line = " ".join(value.split())
    return single_line[:limit] + ("..." if len(single_line) > limit else "")


def _summarize_tool_arguments(arguments: dict) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        key_text = str(key)
        key_lower = key_text.lower()

        if key_lower in SENSITIVE_ARGUMENT_KEYS or any(part in key_lower for part in SENSITIVE_ARGUMENT_KEYS):
            summary[key_text] = "<redacted>"
        elif key_lower == "connection_string":
            summary[key_text] = _summarize_connection_string(str(value))
        elif key_lower == "query":
            summary[key_text] = _summarize_sql(str(value))
        elif "url" in key_lower and isinstance(value, str):
            summary[key_text] = _summarize_url(value)
        elif key_lower in {"parameters", "params"} and isinstance(value, dict):
            summary[key_text] = {"keys": sorted(str(k) for k in value)}
        elif key_lower in {"content", "body"} and isinstance(value, str):
            summary[key_text] = {"length": len(value)}
        elif isinstance(value, str):
            summary[key_text] = _safe_preview(value)
        elif isinstance(value, (int, float, bool)) or value is None:
            summary[key_text] = value
        elif isinstance(value, list):
            summary[key_text] = {"type": "list", "length": len(value)}
        elif isinstance(value, dict):
            summary[key_text] = {"type": "object", "keys": sorted(str(k) for k in value)}
        else:
            summary[key_text] = {"type": type(value).__name__}
    return summary


def _summarize_tool_result(result: Any) -> dict[str, Any]:
    if isinstance(result, str):
        return {"type": "string", "length": len(result), "preview": _safe_preview(result, 240)}
    if isinstance(result, dict):
        return {"type": "object", "keys": sorted(str(k) for k in result)}
    if isinstance(result, list):
        return {"type": "list", "length": len(result)}
    if result is None:
        return {"type": "none"}
    return {"type": type(result).__name__}


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

        async with httpx.AsyncClient() as client, client.stream(
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

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        headers: dict = None,
        preserve_reasoning_content: bool = False,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.extra_headers = headers or {}
        self.preserve_reasoning_content = preserve_reasoning_content

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
                reasoning_content=message.get("reasoning_content"),
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

        async with httpx.AsyncClient() as client, client.stream(
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

        async with httpx.AsyncClient() as client, client.stream(
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


@dataclass(frozen=True)
class ProviderCapabilities:
    """Runtime capability metadata for provider selection and admin UI display."""

    protocol: str
    default_base_url: str
    well_known_models: tuple[str, ...] = ()
    supports_tool_calls: bool = True
    supports_streaming: bool = True
    preserves_reasoning_content: bool = False
    recommended_for: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "default_base_url": self.default_base_url,
            "well_known_models": list(self.well_known_models),
            "supports_tool_calls": self.supports_tool_calls,
            "supports_streaming": self.supports_streaming,
            "preserves_reasoning_content": self.preserves_reasoning_content,
            "recommended_for": list(self.recommended_for),
        }


PROVIDER_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "anthropic": ProviderCapabilities(
        protocol="anthropic",
        default_base_url="https://api.anthropic.com",
        well_known_models=("claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-4-20250506"),
        recommended_for=("general_reasoning", "agentic_tasks"),
    ),
    "openai": ProviderCapabilities(
        protocol="openai_compatible",
        default_base_url="https://api.openai.com/v1",
        well_known_models=("gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o3-mini"),
        recommended_for=("general_reasoning", "tool_use"),
    ),
    "minimax": ProviderCapabilities(
        protocol="anthropic_compatible",
        default_base_url="https://api.minimaxi.com/anthropic/v1",
        well_known_models=("MiniMax-M1-80k", "MiniMax-M2.7-highspeed"),
        recommended_for=("long_context", "multi_agent_collaboration"),
    ),
    "kimi": ProviderCapabilities(
        protocol="openai_compatible",
        default_base_url="https://api.kimi.com/coding/v1",
        well_known_models=("kimi-coder", "kimi-thinking-coder", "kimi-latest"),
        preserves_reasoning_content=True,
        recommended_for=("coding", "agentic_tasks"),
    ),
    "deepseek": ProviderCapabilities(
        protocol="openai_compatible",
        default_base_url="https://api.deepseek.com",
        well_known_models=("deepseek-v4-pro",),
        preserves_reasoning_content=True,
        recommended_for=("reasoning", "agentic_tasks", "multi_agent_collaboration"),
    ),
    "dashscope": ProviderCapabilities(
        protocol="openai_compatible",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        well_known_models=("qwen3.6-plus", "qwen-plus", "qwen-turbo"),
        recommended_for=("general_reasoning", "multimodal_extensions"),
    ),
}


SUPPORTED_PROVIDER_TYPES = frozenset(PROVIDER_CAPABILITIES)


def get_provider_capabilities(provider_type: str) -> ProviderCapabilities | None:
    return PROVIDER_CAPABILITIES.get(provider_type.lower())


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
        "deepseek": OpenAIProvider,  # DeepSeek 使用 OpenAI 兼容 API
        "dashscope": OpenAIProvider,  # 阿里云 DashScope 使用 OpenAI 兼容 API
    }

    ptype = provider_type.lower()
    if ptype not in SUPPORTED_PROVIDER_TYPES:
        raise ValueError(f"Unknown provider: {provider_type}")
    provider_class = providers.get(ptype)

    call_kwargs = dict(kwargs)
    capabilities = get_provider_capabilities(ptype)

    if base_url:
        call_kwargs["base_url"] = base_url
    elif capabilities and capabilities.default_base_url:
        call_kwargs["base_url"] = capabilities.default_base_url
    if headers and capabilities and capabilities.protocol == "openai_compatible":
        call_kwargs["headers"] = headers
    if capabilities and capabilities.preserves_reasoning_content:
        call_kwargs["preserve_reasoning_content"] = True
    return provider_class(api_key, **call_kwargs)
