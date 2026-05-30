from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from agents.worker_pool import SubTask, Task, TaskStatus
from auth import init_auth
from coordinator.iterative_orchestrator import IterationRecord, IterationResult
from scheduler.runner import ScheduledTaskRunner
from storage.persistence import PersistenceStore


class ScheduledDeterministicOrchestrator:
    """No-network orchestrator used to prove scheduled tasks enter task jobs."""

    def __init__(self, shared_dir: Path):
        self._shared_dir = shared_dir
        self._running: set[str] = set()
        self.calls = 0
        self.context: dict[str, Any] = {}
        self.active_group_ids: list[str] | None = None

    async def run(
        self,
        description: str,
        context: dict[str, Any] | None = None,
        active_group_ids: list[str] | None = None,
        task_id: str | None = None,
        on_task_update=None,
    ) -> IterationResult:
        assert task_id
        self.calls += 1
        self.context = dict(context or {})
        self.active_group_ids = active_group_ids
        self._running.add(task_id)
        try:
            task = Task(
                id=task_id,
                description=description,
                sub_tasks=[
                    SubTask(
                        id="sub_scheduled",
                        description="Execute scheduled task payload",
                        acceptance_criteria=["source context persisted", "active groups forwarded"],
                        assigned_agent="scheduler-worker",
                    )
                ],
            )
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now().isoformat()
            await self._emit(
                on_task_update,
                task,
                "planned",
                {
                    "subtask_count": 1,
                    "source": self.context.get("source"),
                    "scheduled_task_id": self.context.get("scheduled_task_id"),
                    "active_group_ids": active_group_ids or [],
                },
            )

            subtask = task.sub_tasks[0]
            subtask.status = TaskStatus.RUNNING
            subtask.started_at = datetime.now().isoformat()
            subtask.attempts = 1
            await self._emit(
                on_task_update,
                task,
                "subtask_running",
                {
                    "subtask_id": subtask.id,
                    "worker_id": "scheduler-worker",
                    "provider": "qwen",
                    "model": "qwen3.7",
                    "attempt": 1,
                },
            )
            subtask.status = TaskStatus.COMPLETED
            subtask.result = "Scheduled task completed deterministically."
            subtask.completed_at = datetime.now().isoformat()
            task.status = TaskStatus.COMPLETED
            task.result = "Scheduled background task completed."
            task.completed_at = datetime.now().isoformat()
            await self._emit(
                on_task_update,
                task,
                "subtask_completed",
                {"subtask_id": subtask.id, "worker_id": "scheduler-worker", "attempt": 1},
            )
            await self._emit(on_task_update, task, "task_completed", {"quality_score": 0.9})

            self._shared_dir.mkdir(parents=True, exist_ok=True)
            (self._shared_dir / "scheduled.md").write_text("Scheduled task output.\n", encoding="utf-8")
            return IterationResult(
                task_id=task_id,
                shared_dir=str(self._shared_dir),
                final_score=0.9,
                iterations=[IterationRecord(iteration=0, score=0.9, improvements=[])],
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


def _client_and_runner(
    monkeypatch: pytest.MonkeyPatch,
    store: PersistenceStore,
    orchestrator: ScheduledDeterministicOrchestrator,
) -> tuple[TestClient, ScheduledTaskRunner, dict[str, str]]:
    from src.web import api as api_module
    from web.routers import scheduled as scheduled_router
    from web.routers import tasks as tasks_router

    monkeypatch.setattr(scheduled_router, "_gs", lambda: store)
    monkeypatch.setattr(tasks_router, "_gs", lambda: store)
    api_module._orchestrator = orchestrator
    api_module._task_planner = None
    api_module._task_results = {}
    tasks_router._task_job_runner = None
    tasks_router._task_job_runner_key = None
    task_runner = tasks_router.init_task_job_runner(orchestrator, None, store, api_module._task_results)

    app = FastAPI()
    app.include_router(scheduled_router.router)
    app.include_router(tasks_router.router)
    auth = init_auth(
        [{"username": "admin", "password": "pw", "role": "admin", "display_name": "Admin"}],
        app_state=app.state,
    )
    token = auth.login("admin", "pw")
    assert token
    return TestClient(app, raise_server_exceptions=False), ScheduledTaskRunner(store, task_runner), {
        "Authorization": f"Bearer {token}"
    }


async def _wait_for_persisted_task(store: PersistenceStore, description: str, status: str) -> dict[str, Any]:
    for _ in range(100):
        for task in store.list_tasks(limit=10):
            if task["description"] == description and task["status"] == status:
                return task
        await asyncio.sleep(0.02)
    raise AssertionError(f"scheduled task history did not reach {status}")


@pytest.mark.asyncio
async def test_scheduled_task_api_fires_into_persistent_task_queue(monkeypatch, tmp_path: Path) -> None:
    store = PersistenceStore(tmp_path / "scheduled.db")
    orchestrator = ScheduledDeterministicOrchestrator(tmp_path / "shared" / "scheduled")
    client, scheduled_runner, headers = _client_and_runner(monkeypatch, store, orchestrator)
    description = "Run deterministic scheduled report"

    try:
        created = client.post(
            "/api/scheduled-tasks",
            headers=headers,
            json={
                "description": description,
                "cron": "* * * * *",
                "enabled": True,
                "active_group_ids": ["group_scheduled"],
            },
        )
        assert created.status_code == 200
        scheduled = created.json()
        assert scheduled["description"] == description
        assert scheduled["active_group_ids"] == ["group_scheduled"]

        await scheduled_runner._tick()
        persisted = await _wait_for_persisted_task(store, description, "completed")
        task_id = persisted["task_id"]

        assert orchestrator.calls == 1
        assert orchestrator.context == {
            "source": "scheduled_task",
            "scheduled_task_id": scheduled["id"],
        }
        assert orchestrator.active_group_ids == ["group_scheduled"]

        scheduled_after_fire = store.get_scheduled_task(scheduled["id"])
        assert scheduled_after_fire["last_run_at"]
        assert scheduled_after_fire["next_run_at"]

        request_record = store.get_task_job_request(task_id)
        assert request_record["description"] == description
        assert request_record["context"] == {
            "source": "scheduled_task",
            "scheduled_task_id": scheduled["id"],
        }
        assert request_record["active_group_ids"] == ["group_scheduled"]
        assert request_record["generate_suggestions"] is True
        assert request_record["lease_owner"] == ""

        detail = client.get(f"/api/tasks/{task_id}")
        assert detail.status_code == 200
        detail_payload = detail.json()
        assert detail_payload["status"] == "completed"
        assert detail_payload["sub_tasks"][0]["assigned_agent"] == "scheduler-worker"

        trace = client.get(f"/api/tasks/{task_id}/trace")
        assert trace.status_code == 200
        trace_payload = trace.json()
        assert trace_payload["summary"]["subtask_count"] == 1
        planned_event = next(event for event in trace_payload["timeline"] if event["event_type"] == "planned")
        assert planned_event["details"]["source"] == "scheduled_task"
        assert planned_event["details"]["scheduled_task_id"] == scheduled["id"]

        await scheduled_runner._tick()
        await asyncio.sleep(0.05)
        assert len([task for task in store.list_tasks(limit=10) if task["description"] == description]) == 1
    finally:
        scheduled_runner.stop()
        client.close()
        store.close()
