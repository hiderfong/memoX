from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from agents.worker_pool import SubTask, Task, TaskStatus
from coordinator.iterative_orchestrator import IterationRecord, IterationResult
from coordinator.task_jobs import TaskJobRunner
from storage.persistence import PersistenceStore


class DeterministicMultiAgentOrchestrator:
    """A no-network orchestrator that exercises the API runner like a real task."""

    def __init__(self, store: PersistenceStore, shared_dir: Path):
        self._store = store
        self._shared_dir = shared_dir
        self._running: set[str] = set()
        self.cancelled: set[str] = set()

    async def run(
        self,
        description: str,
        context: dict[str, Any] | None = None,
        active_group_ids: list[str] | None = None,
        task_id: str | None = None,
        on_task_update=None,
    ) -> IterationResult:
        assert task_id
        self._running.add(task_id)
        try:
            task = Task(
                id=task_id,
                description=description,
                sub_tasks=[
                    SubTask(
                        id="sub_research",
                        description="Collect durable execution evidence",
                        acceptance_criteria=["source list", "execution risk summary"],
                        assigned_agent="researcher",
                    ),
                    SubTask(
                        id="sub_synthesis",
                        description="Synthesize execution plan from evidence",
                        dependencies=["sub_research"],
                        acceptance_criteria=["prioritized plan", "fallback notes"],
                        assigned_agent="writer",
                    ),
                ],
            )
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now().isoformat()
            await self._emit(
                on_task_update,
                task,
                "planned",
                {
                    "subtask_count": 2,
                    "mode": "deterministic_fake",
                    "active_group_ids": active_group_ids or [],
                    "context_keys": sorted((context or {}).keys()),
                },
            )

            research = task.sub_tasks[0]
            research.status = TaskStatus.RUNNING
            research.started_at = datetime.now().isoformat()
            research.attempts = 1
            await self._emit(
                on_task_update,
                task,
                "subtask_running",
                {
                    "subtask_id": research.id,
                    "worker_id": "researcher",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "attempt": 1,
                },
            )
            await self._emit(
                on_task_update,
                task,
                "llm_usage",
                {
                    "subtask_id": research.id,
                    "worker_id": "researcher",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "input_tokens": 120,
                    "output_tokens": 44,
                    "total_tokens": 164,
                    "call_count": 1,
                },
            )
            self._store.log_audit_event(
                action="tool_call",
                resource="tool",
                resource_id="web_search",
                username="researcher",
                user_role="worker",
                details={
                    "task_id": task_id,
                    "subtask_id": research.id,
                    "worker_id": "researcher",
                    "status": "success",
                    "arguments": {"query": "durable multi agent execution"},
                    "result": {"preview": "3 deterministic references"},
                },
            )
            self._store.add_worker_log(
                "researcher",
                "info",
                "Deterministic evidence collected",
                {"task_id": task_id, "subtask_id": research.id, "phase": "collect"},
            )
            research.status = TaskStatus.COMPLETED
            research.result = "Evidence collected without external providers."
            research.completed_at = datetime.now().isoformat()
            await self._emit(
                on_task_update,
                task,
                "subtask_completed",
                {"subtask_id": research.id, "worker_id": "researcher", "attempt": 1},
            )

            await asyncio.sleep(0)

            synthesis = task.sub_tasks[1]
            synthesis.status = TaskStatus.RUNNING
            synthesis.started_at = datetime.now().isoformat()
            synthesis.attempts = 1
            await self._emit(
                on_task_update,
                task,
                "subtask_running",
                {
                    "subtask_id": synthesis.id,
                    "worker_id": "writer",
                    "provider": "minimax",
                    "model": "MiniMax-M1",
                    "attempt": 1,
                },
            )
            await self._emit(
                on_task_update,
                task,
                "provider_retry",
                {
                    "subtask_id": synthesis.id,
                    "worker_id": "writer",
                    "provider": "minimax",
                    "model": "MiniMax-M1",
                    "attempt": 1,
                    "error": "HTTP 429",
                },
            )
            await self._emit(
                on_task_update,
                task,
                "provider_fallback",
                {
                    "subtask_id": synthesis.id,
                    "worker_id": "writer",
                    "provider": "qwen",
                    "model": "qwen3.7",
                },
            )
            await self._emit(
                on_task_update,
                task,
                "llm_usage",
                {
                    "subtask_id": synthesis.id,
                    "worker_id": "writer",
                    "provider": "qwen",
                    "model": "qwen3.7",
                    "input_tokens": 90,
                    "output_tokens": 35,
                    "total_tokens": 125,
                    "call_count": 1,
                },
            )
            self._store.log_audit_event(
                action="tool_call",
                resource="tool",
                resource_id="web_fetch",
                username="writer",
                user_role="worker",
                details={
                    "task_id": task_id,
                    "subtask_id": synthesis.id,
                    "worker_id": "writer",
                    "status": "success",
                    "arguments": {"url": "https://example.invalid/reference"},
                    "result": {"preview": "cached deterministic content"},
                },
            )
            synthesis.status = TaskStatus.COMPLETED
            synthesis.result = "Plan synthesized with provider fallback accounted for."
            synthesis.completed_at = datetime.now().isoformat()
            task.status = TaskStatus.COMPLETED
            task.result = "Deterministic multi-agent task completed."
            task.completed_at = datetime.now().isoformat()
            await self._emit(
                on_task_update,
                task,
                "subtask_completed",
                {"subtask_id": synthesis.id, "worker_id": "writer", "attempt": 1},
            )
            await self._emit(on_task_update, task, "task_completed", {"quality_score": 0.93})

            self._shared_dir.mkdir(parents=True, exist_ok=True)
            (self._shared_dir / "mail_log.txt").write_text(
                "researcher -> writer: evidence ready\nwriter -> researcher: synthesis done\n",
                encoding="utf-8",
            )
            (self._shared_dir / "plan.md").write_text(
                "# Deterministic Plan\n\n- Persist jobs\n- Preserve trace\n- Surface fallback\n",
                encoding="utf-8",
            )
            return IterationResult(
                task_id=task_id,
                shared_dir=str(self._shared_dir),
                final_score=0.93,
                iterations=[
                    IterationRecord(
                        iteration=0,
                        score=0.93,
                        improvements=["Provider fallback surfaced in trace"],
                    )
                ],
                result_summary=task.result,
            )
        finally:
            self._running.discard(task_id)

    async def _emit(self, callback, task: Task, event_type: str, details: dict[str, Any]) -> None:
        if callback:
            await callback(task, event_type, details)

    def cancel_task(self, task_id: str) -> bool:
        self.cancelled.add(task_id)
        return False

    def list_running_tasks(self) -> list[str]:
        return sorted(self._running)

    def is_waiting_feedback(self, task_id: str) -> bool:
        return False

    def submit_feedback(self, task_id: str, feedback: str) -> bool:
        return False


class RetryThenSuccessOrchestrator:
    """Fail once with a retryable provider-like error, then finish from checkpoint."""

    def __init__(self, shared_dir: Path):
        self._shared_dir = shared_dir
        self._running: set[str] = set()
        self.calls = 0
        self.contexts: list[dict[str, Any]] = []

    async def run(
        self,
        description: str,
        context: dict[str, Any] | None = None,
        active_group_ids: list[str] | None = None,
        task_id: str | None = None,
        on_task_update=None,
    ) -> IterationResult:
        assert task_id
        ctx = dict(context or {})
        self.contexts.append(ctx)
        self.calls += 1
        self._running.add(task_id)
        try:
            task = Task(
                id=task_id,
                description=description,
                sub_tasks=[
                    SubTask(
                        id="sub_retry",
                        description="Call a flaky provider and recover",
                        acceptance_criteria=["retryable failure is classified", "retry resumes checkpoint"],
                        assigned_agent="researcher",
                    )
                ],
            )
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now().isoformat()
            task.sub_tasks[0].status = TaskStatus.RUNNING
            task.sub_tasks[0].attempts = self.calls
            task.sub_tasks[0].started_at = datetime.now().isoformat()
            await self._emit(
                on_task_update,
                task,
                "planned",
                {
                    "subtask_count": 1,
                    "resumed_from_checkpoint": "_resume_checkpoint" in ctx,
                    "active_group_ids": active_group_ids or [],
                },
            )
            await self._emit(
                on_task_update,
                task,
                "subtask_running",
                {
                    "subtask_id": "sub_retry",
                    "worker_id": "researcher",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "attempt": self.calls,
                },
            )

            if self.calls == 1:
                await self._emit(
                    on_task_update,
                    task,
                    "provider_retry",
                    {
                        "subtask_id": "sub_retry",
                        "worker_id": "researcher",
                        "provider": "deepseek",
                        "model": "deepseek-v4-pro",
                        "attempt": 1,
                        "error": "network unavailable",
                    },
                )
                raise OSError("network unavailable")

            task.sub_tasks[0].status = TaskStatus.COMPLETED
            task.sub_tasks[0].result = "Recovered after manual retry."
            task.sub_tasks[0].completed_at = datetime.now().isoformat()
            task.status = TaskStatus.COMPLETED
            task.result = "Retryable task recovered through API retry."
            task.completed_at = datetime.now().isoformat()
            await self._emit(
                on_task_update,
                task,
                "subtask_completed",
                {"subtask_id": "sub_retry", "worker_id": "researcher", "attempt": self.calls},
            )
            await self._emit(on_task_update, task, "task_completed", {"quality_score": 0.89})

            self._shared_dir.mkdir(parents=True, exist_ok=True)
            (self._shared_dir / "retry.md").write_text("Recovered after API retry.\n", encoding="utf-8")
            return IterationResult(
                task_id=task_id,
                shared_dir=str(self._shared_dir),
                final_score=0.89,
                iterations=[IterationRecord(iteration=0, score=0.89, improvements=["Manual retry succeeded"])],
                result_summary=task.result,
            )
        finally:
            self._running.discard(task_id)

    async def _emit(self, callback, task: Task, event_type: str, details: dict[str, Any]) -> None:
        if callback:
            await callback(task, event_type, details)

    def cancel_task(self, task_id: str) -> bool:
        return False

    def list_running_tasks(self) -> list[str]:
        return sorted(self._running)

    def is_waiting_feedback(self, task_id: str) -> bool:
        return False

    def submit_feedback(self, task_id: str, feedback: str) -> bool:
        return False


class ResumeCheckpointOrchestrator:
    """Resume a persisted checkpoint after a simulated process restart."""

    def __init__(self, shared_dir: Path):
        self._shared_dir = shared_dir
        self._running: set[str] = set()
        self.resume_checkpoint: dict[str, Any] | None = None

    async def run(
        self,
        description: str,
        context: dict[str, Any] | None = None,
        active_group_ids: list[str] | None = None,
        task_id: str | None = None,
        on_task_update=None,
    ) -> IterationResult:
        assert task_id
        ctx = dict(context or {})
        self.resume_checkpoint = ctx.get("_resume_checkpoint")
        assert self.resume_checkpoint
        self._running.add(task_id)
        try:
            task = Task(
                id=task_id,
                description=description,
                sub_tasks=[
                    SubTask(
                        id="sub_done",
                        description="Already persisted research",
                        status=TaskStatus.COMPLETED,
                        result="Cached evidence",
                        assigned_agent="researcher",
                        attempts=1,
                    ),
                    SubTask(
                        id="sub_resume",
                        description="Continue writing from checkpoint",
                        dependencies=["sub_done"],
                        acceptance_criteria=["final answer"],
                        assigned_agent="writer",
                    ),
                ],
            )
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now().isoformat()
            await self._emit(
                on_task_update,
                task,
                "planned",
                {"subtask_count": 2, "resumed_from_checkpoint": True, "active_group_ids": active_group_ids or []},
            )

            resumed = task.sub_tasks[1]
            resumed.status = TaskStatus.RUNNING
            resumed.started_at = datetime.now().isoformat()
            resumed.attempts = 1
            await self._emit(
                on_task_update,
                task,
                "subtask_running",
                {
                    "subtask_id": resumed.id,
                    "worker_id": "writer",
                    "provider": "qwen",
                    "model": "qwen3.7",
                    "attempt": 1,
                },
            )
            resumed.status = TaskStatus.COMPLETED
            resumed.result = "Recovered continuation completed."
            resumed.completed_at = datetime.now().isoformat()
            task.status = TaskStatus.COMPLETED
            task.result = "Recovered pending job from checkpoint."
            task.completed_at = datetime.now().isoformat()
            await self._emit(
                on_task_update,
                task,
                "subtask_completed",
                {"subtask_id": resumed.id, "worker_id": "writer", "attempt": 1},
            )
            await self._emit(on_task_update, task, "task_completed", {"quality_score": 0.91})

            self._shared_dir.mkdir(parents=True, exist_ok=True)
            (self._shared_dir / "recovered.md").write_text("Recovered from checkpoint.\n", encoding="utf-8")
            return IterationResult(
                task_id=task_id,
                shared_dir=str(self._shared_dir),
                final_score=0.91,
                iterations=[IterationRecord(iteration=0, score=0.91, improvements=["Checkpoint resumed"])],
                result_summary=task.result,
            )
        finally:
            self._running.discard(task_id)

    async def _emit(self, callback, task: Task, event_type: str, details: dict[str, Any]) -> None:
        if callback:
            await callback(task, event_type, details)

    def cancel_task(self, task_id: str) -> bool:
        return False

    def list_running_tasks(self) -> list[str]:
        return sorted(self._running)


def _client_with_orchestrator(
    monkeypatch,
    store: PersistenceStore,
    orchestrator: Any,
) -> TestClient:
    from src.web import api as api_module
    from web.routers import tasks as tasks_router

    monkeypatch.setattr(tasks_router, "_gs", lambda: store)
    api_module._orchestrator = orchestrator
    api_module._task_planner = None
    api_module._task_results = {}
    tasks_router._task_job_runner = None
    tasks_router._task_job_runner_key = None

    app = FastAPI()
    app.include_router(tasks_router.router)
    return TestClient(app, raise_server_exceptions=False)


def _wait_for_api_status(client: TestClient, task_id: str, expected: str) -> dict[str, Any]:
    for _ in range(100):
        response = client.get(f"/api/tasks/{task_id}")
        if response.status_code == 200:
            payload = response.json()
            if payload["status"] == expected:
                return payload
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {expected}")


async def _wait_until_runner_done(runner: TaskJobRunner) -> None:
    for _ in range(100):
        if not runner.list_running():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("task runner did not finish")


def test_deterministic_multiagent_task_api_persists_trace_and_files(monkeypatch, tmp_path: Path) -> None:
    store = PersistenceStore(tmp_path / "memox.db")
    orchestrator = DeterministicMultiAgentOrchestrator(store, tmp_path / "shared" / "task")
    client = _client_with_orchestrator(monkeypatch, store, orchestrator)

    try:
        accepted = client.post(
            "/api/tasks",
            json={
                "description": "Validate durable multi-agent execution",
                "context": {"source": "deterministic_e2e", "release_gate": True},
                "generate_suggestions": False,
                "active_group_ids": ["group_release"],
            },
        )
        assert accepted.status_code == 200
        task_id = accepted.json()["task_id"]
        assert accepted.json()["status"] == "queued"

        completed = _wait_for_api_status(client, task_id, "completed")
        assert completed["result"] == "Deterministic multi-agent task completed."
        assert completed["final_score"] == 0.93
        assert completed["job"]["lease_owner"] == ""
        assert [subtask["id"] for subtask in completed["sub_tasks"]] == ["sub_research", "sub_synthesis"]
        assert {subtask["assigned_agent"] for subtask in completed["sub_tasks"]} == {"researcher", "writer"}

        request_record = store.get_task_job_request(task_id)
        assert request_record["context"]["source"] == "deterministic_e2e"
        assert request_record["active_group_ids"] == ["group_release"]
        assert request_record["generate_suggestions"] is False

        events = client.get(f"/api/tasks/{task_id}/events")
        assert events.status_code == 200
        event_types = [event["event_type"] for event in events.json()["events"]]
        assert event_types[:2] == ["queued", "running"]
        assert "planned" in event_types
        assert "provider_retry" in event_types
        assert "provider_fallback" in event_types
        assert event_types[-1] == "completed"

        trace = client.get(f"/api/tasks/{task_id}/trace")
        assert trace.status_code == 200
        trace_payload = trace.json()
        assert trace_payload["summary"]["subtask_count"] == 2
        assert trace_payload["summary"]["retry_count"] == 1
        assert trace_payload["summary"]["fallback_count"] == 1
        assert trace_payload["summary"]["tool_call_count"] == 2
        assert trace_payload["summary"]["worker_log_count"] == 1
        assert trace_payload["summary"]["llm_usage"] == {
            "input_tokens": 210,
            "output_tokens": 79,
            "total_tokens": 289,
            "call_count": 2,
        }
        assert [subtask["id"] for subtask in trace_payload["subtasks"]] == ["sub_research", "sub_synthesis"]
        writer_events = trace_payload["subtasks"][1]["events"]
        assert [event["event_type"] for event in writer_events if event["stage"] == "provider"] == [
            "provider_retry",
            "provider_fallback",
        ]
        writer_tool_events = [event for event in writer_events if event["event_type"] == "tool_call"]
        assert len(writer_tool_events) == 1
        assert writer_tool_events[0]["details"]["tool"] == "web_fetch"

        provider_trace = client.get(f"/api/tasks/{task_id}/trace", params={"stage": "provider"})
        assert provider_trace.status_code == 200
        assert provider_trace.json()["summary"]["event_count"] == 2

        files = client.get(f"/api/tasks/{task_id}/files")
        assert files.status_code == 200
        assert [item["path"] for item in files.json()["files"]] == ["plan.md"]

        listed = client.get("/api/tasks")
        assert listed.status_code == 200
        assert listed.json()[0]["task_id"] == task_id
        assert client.get("/api/tasks/running").json() == []
    finally:
        client.close()
        store.close()


def test_deterministic_retryable_failure_can_be_retried_through_api(monkeypatch, tmp_path: Path) -> None:
    store = PersistenceStore(tmp_path / "retry.db")
    orchestrator = RetryThenSuccessOrchestrator(tmp_path / "shared" / "retry")
    client = _client_with_orchestrator(monkeypatch, store, orchestrator)

    try:
        accepted = client.post(
            "/api/tasks",
            json={
                "description": "Recover a retryable provider failure",
                "context": {"source": "retry_e2e"},
                "generate_suggestions": False,
            },
        )
        assert accepted.status_code == 200
        task_id = accepted.json()["task_id"]

        failed = _wait_for_api_status(client, task_id, "failed")
        assert "可重试" in failed["result"]
        assert failed["last_failure"]["failure_type"] == "retryable_exception"
        assert failed["last_failure"]["retryable"] is True

        suggestion = client.get(f"/api/tasks/{task_id}/retry-suggestion")
        assert suggestion.status_code == 200
        suggestion_payload = suggestion.json()
        assert suggestion_payload["mode"] == "manual_retry"
        assert suggestion_payload["retryable"] is True
        assert suggestion_payload["retry_request"]["path"] == f"/api/tasks/{task_id}/retry"

        retry = client.post(f"/api/tasks/{task_id}/retry", json={"force": False})
        assert retry.status_code == 200
        assert retry.json()["status"] == "queued"

        completed = _wait_for_api_status(client, task_id, "completed")
        assert completed["result"] == "Retryable task recovered through API retry."
        assert completed["final_score"] == 0.89
        assert orchestrator.calls == 2
        assert orchestrator.contexts[1]["_resume_checkpoint"]["task_id"] == task_id

        request_record = store.get_task_job_request(task_id)
        assert request_record["recovery_count"] == 1
        assert request_record["lease_owner"] == ""

        events = client.get(f"/api/tasks/{task_id}/events").json()["events"]
        event_types = [event["event_type"] for event in events]
        assert "failed_retryable" in event_types
        assert "retry_queued" in event_types
        assert event_types[-1] == "completed"

        trace = client.get(f"/api/tasks/{task_id}/trace")
        assert trace.status_code == 200
        trace_payload = trace.json()
        assert trace_payload["summary"]["retry_count"] == 2
        assert {"provider_retry", "failed_retryable", "retry_queued"}.issubset(
            {event["event_type"] for event in trace_payload["timeline"]}
        )
        assert trace_payload["subtasks"][0]["events"][0]["event_type"] == "subtask_running"
    finally:
        client.close()
        store.close()


@pytest.mark.asyncio
async def test_deterministic_recover_pending_resumes_checkpoint_after_restart(monkeypatch, tmp_path: Path) -> None:
    store = PersistenceStore(tmp_path / "recover.db")
    task_id = "task_recover_e2e"
    created_at = "2026-05-31T09:00:00"
    store.save_task(
        {
            "task_id": task_id,
            "description": "Recover pending deterministic job",
            "status": "running",
            "created_at": created_at,
        }
    )
    store.save_task_job_request(
        task_id,
        "Recover pending deterministic job",
        context={"source": "recover_e2e"},
        active_group_ids=["group_recover"],
        created_at=created_at,
    )
    store.save_task_checkpoint(
        task_id,
        {
            "task_id": task_id,
            "description": "Recover pending deterministic job",
            "status": "running",
            "sub_tasks": [
                {
                    "id": "sub_done",
                    "description": "Already persisted research",
                    "status": "completed",
                    "result": "Cached evidence",
                    "assigned_agent": "researcher",
                    "attempts": 1,
                },
                {
                    "id": "sub_resume",
                    "description": "Continue writing from checkpoint",
                    "dependencies": ["sub_done"],
                    "status": "running",
                    "assigned_agent": "writer",
                    "attempts": 0,
                },
            ],
        },
    )

    orchestrator = ResumeCheckpointOrchestrator(tmp_path / "shared" / "recover")
    runner = TaskJobRunner(orchestrator, None, store, {}, owner_id="recover_e2e")
    client = None

    try:
        assert runner.recover_pending() == [task_id]
        await _wait_until_runner_done(runner)

        assert orchestrator.resume_checkpoint["task_id"] == task_id
        request_record = store.get_task_job_request(task_id)
        assert request_record["recovery_count"] == 1
        assert request_record["lease_owner"] == ""
        persisted = store.get_task(task_id)
        assert persisted["status"] == "completed"
        assert persisted["created_at"] == created_at
        assert persisted["result"] == "Recovered pending job from checkpoint."

        client = _client_with_orchestrator(monkeypatch, store, orchestrator)
        detail = client.get(f"/api/tasks/{task_id}")
        assert detail.status_code == 200
        assert [subtask["status"] for subtask in detail.json()["sub_tasks"]] == ["completed", "completed"]

        trace = client.get(f"/api/tasks/{task_id}/trace")
        assert trace.status_code == 200
        trace_payload = trace.json()
        assert trace_payload["summary"]["subtask_count"] == 2
        assert [event["event_type"] for event in trace_payload["unassigned_events"][:2]] == [
            "recovered",
            "running",
        ]
        planned_events = [event for event in trace_payload["timeline"] if event["event_type"] == "planned"]
        assert planned_events[-1]["details"]["resumed_from_checkpoint"] is True
    finally:
        if client:
            client.close()
        store.close()
