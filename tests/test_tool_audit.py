from typing import Any

import pytest

from src.agents import base_agent as base_agent_module
from src.agents.base_agent import BaseTool, ToolRegistry


class _FakeStore:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log_audit_event(self, **kwargs) -> None:
        self.events.append(kwargs)


class _StaticTool(BaseTool):
    def __init__(self, name: str, result: Any) -> None:
        self._name = name
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Static test tool"

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict) -> Any:
        return self._result


@pytest.mark.asyncio
async def test_tool_registry_audits_success_without_leaking_sensitive_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(base_agent_module, "_get_persistence_store", lambda: store)

    registry = ToolRegistry(audit_context={"worker_id": "researcher", "task_id": "task_1"})
    registry.register(_StaticTool("database_query", '{"ok": true}'))

    result = await registry.execute("database_query", {
        "connection_string": "postgresql://alice:secret@db.internal:5432/app?sslmode=require",
        "url": "https://alice:secret@example.com/page?token=secret#frag",
        "query": "SELECT * FROM users WHERE token = 'secret'",
        "parameters": {"token": "secret"},
        "content": "large private content",
    })

    assert result == '{"ok": true}'
    event = store.events[0]
    assert event["action"] == "tool_call"
    assert event["resource"] == "tool"
    assert event["resource_id"] == "database_query"
    assert event["username"] == "researcher"
    assert event["user_role"] == "worker"

    details = event["details"]
    assert details["status"] == "success"
    assert details["worker_id"] == "researcher"
    assert details["task_id"] == "task_1"
    assert details["arguments"]["connection_string"] == "postgresql://db.internal:5432/app"
    assert details["arguments"]["url"] == "https://example.com/page"
    assert details["arguments"]["query"]["statement_type"] == "select"
    assert details["arguments"]["parameters"] == {"keys": ["token"]}
    assert details["arguments"]["content"] == {"length": len("large private content")}
    assert "secret" not in str(details["arguments"])


@pytest.mark.asyncio
async def test_tool_registry_marks_policy_rejections(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore()
    monkeypatch.setattr(base_agent_module, "_get_persistence_store", lambda: store)

    registry = ToolRegistry()
    registry.register(_StaticTool("database_query", "Database query rejected: Write SQL requires access_mode='write'"))

    await registry.execute("database_query", {"query": "UPDATE users SET name='x'"})

    assert store.events[0]["details"]["status"] == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "Error: 禁止访问内网、本机或保留 IP 地址",
        "Error: 访问被拒绝，路径不在任务工作区内",
    ],
)
async def test_tool_registry_marks_chinese_policy_errors_as_rejected(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    store = _FakeStore()
    monkeypatch.setattr(base_agent_module, "_get_persistence_store", lambda: store)

    registry = ToolRegistry()
    registry.register(_StaticTool("web_fetch", message))

    await registry.execute("web_fetch", {"url": "http://127.0.0.1/private"})

    assert store.events[0]["details"]["status"] == "rejected"


@pytest.mark.asyncio
async def test_tool_registry_audits_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore()
    monkeypatch.setattr(base_agent_module, "_get_persistence_store", lambda: store)

    registry = ToolRegistry(audit_context={"worker_id": "writer"})

    with pytest.raises(ValueError, match="Unknown tool"):
        await registry.execute("missing_tool", {"token": "secret"})

    event = store.events[0]
    assert event["details"]["status"] == "error"
    assert event["details"]["error"] == "Unknown tool: missing_tool"
    assert event["details"]["arguments"]["token"] == "<redacted>"
