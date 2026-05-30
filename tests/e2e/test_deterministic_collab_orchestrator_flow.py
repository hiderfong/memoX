from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from agents.base_agent import LLMProvider, LLMResponse, ToolCall
from agents.worker_pool import SubTask, Task, WorkerAgent, WorkerConfig, WorkerPool
from coordinator.iterative_orchestrator import IterativeOrchestrator
from coordinator.task_planner import TaskComplexity
from storage.persistence import PersistenceStore


class StaticDependencyPlanner:
    async def plan_task(self, task_description: str, context: dict | None = None):
        task = Task(
            id="task_deterministic_collab",
            description=task_description,
            sub_tasks=[
                SubTask(
                    id="prepare_data",
                    description="Create data.json and notify the processor.",
                    acceptance_criteria=["data.json exists", "processor receives data_ready mail"],
                    assigned_agent="developer",
                ),
                SubTask(
                    id="process_data",
                    description="Read dependency context, write processed.json, and notify reporter.",
                    dependencies=["prepare_data"],
                    acceptance_criteria=["processed.json contains total_items and total_price"],
                    assigned_agent="processor",
                ),
                SubTask(
                    id="write_report",
                    description="Read dependency context and write report.txt.",
                    dependencies=["process_data"],
                    acceptance_criteria=["report.txt includes item names and totals"],
                    assigned_agent="reporter",
                ),
            ],
        )
        return task, TaskComplexity.SEQUENTIAL


class DeterministicToolProvider(LLMProvider):
    """Provider that drives real tools without any network calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict], model: str, **kwargs) -> LLMResponse:
        system = str(messages[0].get("content", ""))
        self.calls.append({"model": model, "system": system[:80], "message_count": len(messages)})

        if "质量评估专家" in system:
            return LLMResponse(
                content=json.dumps({"score": 0.95, "passed": True, "improvements": []}, ensure_ascii=False),
                usage={"input_tokens": 16, "output_tokens": 9},
            )

        worker_name = self._worker_name(system)
        has_tool_result = any(message.get("role") == "tool" for message in messages)
        if has_tool_result:
            return LLMResponse(
                content=f"{worker_name} completed deterministic collaboration step.",
                usage={"input_tokens": 25, "output_tokens": 11},
            )

        tool_calls = self._tool_calls_for(worker_name)
        return LLMResponse(
            content=f"{worker_name} is using deterministic tools.",
            tool_calls=tool_calls,
            finish_reason="tool_calls",
            usage={"input_tokens": 30, "output_tokens": 12},
        )

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        on_chunk=None,
        **kwargs,
    ) -> LLMResponse:
        response = await self.chat(messages, model, **kwargs)
        if on_chunk and response.content:
            on_chunk(response.content)
        return response

    @staticmethod
    def _worker_name(system: str) -> str:
        marker = "# Worker Agent: "
        if marker not in system:
            return "coordinator"
        return system.split(marker, 1)[1].splitlines()[0].strip()

    @staticmethod
    def _tool_calls_for(worker_name: str) -> list[ToolCall]:
        if worker_name == "developer":
            return [
                ToolCall(
                    id="call_dev_write",
                    name="write_file",
                    arguments={
                        "path": "data.json",
                        "content": json.dumps(
                            {
                                "items": [
                                    {"name": "apple", "price": 3},
                                    {"name": "banana", "price": 2},
                                    {"name": "cherry", "price": 5},
                                ]
                            },
                            ensure_ascii=False,
                        ),
                    },
                ),
                ToolCall(
                    id="call_dev_mail",
                    name="send_mail",
                    arguments={"to": "processor", "subject": "data_ready", "body": "data.json created"},
                ),
            ]
        if worker_name == "processor":
            return [
                ToolCall(id="call_proc_mail", name="read_mail", arguments={}),
                ToolCall(
                    id="call_proc_write",
                    name="write_file",
                    arguments={
                        "path": "processed.json",
                        "content": json.dumps(
                            {
                                "total_items": 3,
                                "total_price": 10,
                                "items": [
                                    {"name": "apple", "price": 3},
                                    {"name": "banana", "price": 2},
                                    {"name": "cherry", "price": 5},
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ),
                ToolCall(
                    id="call_proc_notify",
                    name="send_mail",
                    arguments={"to": "reporter", "subject": "processed_ready", "body": "processed.json created"},
                ),
            ]
        if worker_name == "reporter":
            return [
                ToolCall(id="call_report_mail", name="read_mail", arguments={}),
                ToolCall(
                    id="call_report_write",
                    name="write_file",
                    arguments={
                        "path": "report.txt",
                        "content": "商品列表：apple 3元, banana 2元, cherry 5元\n总商品数：3\n总价格：10元\n",
                    },
                ),
            ]
        return []


def _worker_pool(provider: DeterministicToolProvider) -> WorkerPool:
    pool = WorkerPool(max_workers=3)
    for name in ("developer", "processor", "reporter"):
        config = WorkerConfig(
            name=name,
            provider_type="fake",
            api_key="fake",
            model="deterministic-tool-provider",
            temperature=0,
            max_tokens=512,
            max_iterations=4,
            tools=["write_file", "read_file", "send_mail", "read_mail", "list_files"],
        )
        pool.register_worker(WorkerAgent(config=config, provider=provider))
    return pool


@pytest.mark.asyncio
async def test_deterministic_collaboration_uses_real_orchestrator_workers_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import storage.persistence as persistence_module

    store = PersistenceStore(tmp_path / "collab.db")
    monkeypatch.setattr(persistence_module, "_store", store)
    provider = DeterministicToolProvider()
    events: list[tuple[str, dict[str, Any]]] = []

    async def on_task_update(task: Task, event_type: str, details: dict[str, Any]) -> None:
        events.append((event_type, {"task_id": task.id, **details}))

    orchestrator = IterativeOrchestrator(
        planner=StaticDependencyPlanner(),
        worker_pool=_worker_pool(provider),
        provider=provider,
        rag_engine=None,
        model="deterministic-tool-provider",
        temperature=0,
        base_workspace=tmp_path / "workspace",
        max_iterations=1,
        quality_threshold=0.8,
    )

    try:
        result = await asyncio.wait_for(
            orchestrator.run(
                "Create a deterministic three-step data processing report.",
                context={"source": "deterministic_collab_e2e"},
                on_task_update=on_task_update,
            ),
            timeout=10,
        )

        shared = Path(result.shared_dir)
        assert result.status == "completed"
        assert result.final_score == 0.95
        assert result.task_id == "task_deterministic_collab"
        assert shared.exists()

        data = json.loads(next(shared.rglob("data.json")).read_text(encoding="utf-8"))
        processed = json.loads(next(shared.rglob("processed.json")).read_text(encoding="utf-8"))
        report = next(shared.rglob("report.txt")).read_text(encoding="utf-8")
        mail_log = (shared / "mail_log.txt").read_text(encoding="utf-8")

        assert [item["name"] for item in data["items"]] == ["apple", "banana", "cherry"]
        assert processed["total_items"] == 3
        assert processed["total_price"] == 10
        assert "apple 3元" in report
        assert "总价格：10元" in report
        assert "data_ready" in mail_log
        assert "processed_ready" in mail_log

        event_types = [event_type for event_type, _details in events]
        assert event_types[:2] == ["planned", "task_running"]
        assert event_types.count("subtask_completed") == 3
        assert event_types[-1] == "task_completed"
        assert any(
            event_type == "llm_usage" and details["worker_id"] == "developer"
            for event_type, details in events
        )
        assert len(provider.calls) >= 7
    finally:
        store.close()
        persistence_module._store = None
