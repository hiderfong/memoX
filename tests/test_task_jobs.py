import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agents.worker_pool import SubTask, Task, TaskStatus
from coordinator.iterative_orchestrator import IterationRecord, IterationResult
from coordinator.task_jobs import TaskJobRequest, TaskJobRunner
from storage.persistence import PersistenceStore


class FastOrchestrator:
    def __init__(self):
        self.cancelled = set()
        self.context = {}

    async def run(self, description, context=None, active_group_ids=None, task_id=None, on_task_update=None):
        await asyncio.sleep(0)
        assert task_id == "task_fixed"
        self.context = context or {}
        return IterationResult(
            task_id=task_id,
            shared_dir="",
            final_score=0.91,
            iterations=[IterationRecord(iteration=0, score=0.91, improvements=[])],
            result_summary=f"完成: {description}",
        )

    def cancel_task(self, task_id):
        self.cancelled.add(task_id)
        return False

    def list_running_tasks(self):
        return []


class SlowOrchestrator:
    def __init__(self):
        self.cancelled = set()

    async def run(self, description, context=None, active_group_ids=None, task_id=None, on_task_update=None):
        await asyncio.Event().wait()

    def cancel_task(self, task_id):
        self.cancelled.add(task_id)
        return True

    def list_running_tasks(self):
        return []


class CheckpointingOrchestrator(FastOrchestrator):
    async def run(self, description, context=None, active_group_ids=None, task_id=None, on_task_update=None):
        await asyncio.sleep(0)
        task = Task(
            id=task_id,
            description=description,
            sub_tasks=[SubTask(id="sub_1", description="执行子任务", acceptance_criteria=["输出结果"])],
        )
        task.status = TaskStatus.RUNNING
        if on_task_update:
            await on_task_update(task, "planned", {"subtask_count": 1})

        subtask = task.sub_tasks[0]
        subtask.status = TaskStatus.RUNNING
        subtask.attempts = 1
        if on_task_update:
            await on_task_update(task, "subtask_running", {"subtask_id": subtask.id, "attempt": 1})

        subtask.status = TaskStatus.COMPLETED
        subtask.result = "子任务完成"
        task.status = TaskStatus.COMPLETED
        task.result = "全部完成"
        if on_task_update:
            await on_task_update(task, "subtask_completed", {"subtask_id": subtask.id, "attempt": 1})

        return IterationResult(
            task_id=task_id,
            shared_dir="",
            final_score=0.95,
            iterations=[IterationRecord(iteration=0, score=0.95, improvements=[])],
            result_summary="全部完成",
        )


class ErrorOrchestrator(FastOrchestrator):
    def __init__(self, exc: Exception):
        super().__init__()
        self.exc = exc

    async def run(self, description, context=None, active_group_ids=None, task_id=None, on_task_update=None):
        await asyncio.sleep(0)
        raise self.exc


class FailedResultOrchestrator(FastOrchestrator):
    async def run(self, description, context=None, active_group_ids=None, task_id=None, on_task_update=None):
        await asyncio.sleep(0)
        return IterationResult(
            task_id=task_id,
            shared_dir="",
            final_score=0.0,
            iterations=[IterationRecord(iteration=0, score=0.0, improvements=["provider failure"])],
            result_summary="[执行失败]\nConnectError: network unavailable",
            status="failed",
            error="ConnectError: network unavailable",
        )


class FlakyOrchestrator(FastOrchestrator):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def run(self, description, context=None, active_group_ids=None, task_id=None, on_task_update=None):
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(0)
            raise OSError("network unavailable")
        return await super().run(description, context, active_group_ids, task_id, on_task_update)


async def _wait_until_done(runner: TaskJobRunner) -> None:
    for _ in range(100):
        if not runner.list_running():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


async def _wait_for_task_status(store: PersistenceStore, task_id: str, status: str) -> dict:
    for _ in range(200):
        task = store.get_task(task_id)
        if task and task["status"] == status:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach {status}")


@pytest.mark.asyncio
async def test_task_job_runner_persists_queued_and_completed(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    runner = TaskJobRunner(FastOrchestrator(), None, store, {})

    accepted = runner.submit(TaskJobRequest(description="写报告", task_id="task_fixed"))

    assert accepted["task_id"] == "task_fixed"
    assert accepted["status"] == "queued"
    request = store.get_task_job_request("task_fixed")
    assert request["description"] == "写报告"

    await _wait_until_done(runner)
    persisted = store.get_task("task_fixed")
    assert persisted["status"] == "completed"
    assert persisted["result"] == "完成: 写报告"
    assert persisted["final_score"] == 0.91
    request = store.get_task_job_request("task_fixed")
    assert request["lease_owner"] == ""
    assert request["lease_expires_at"] == ""
    event_types = [event["event_type"] for event in store.list_task_events("task_fixed")]
    assert event_types == ["queued", "running", "completed"]
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_records_subtask_checkpoints(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    runner = TaskJobRunner(CheckpointingOrchestrator(), None, store, {})

    runner.submit(TaskJobRequest(description="带子任务", task_id="task_fixed"))
    await _wait_until_done(runner)

    checkpoint = store.get_task_checkpoint("task_fixed")
    assert checkpoint["status"] == "completed"
    assert checkpoint["sub_tasks"][0]["id"] == "sub_1"
    assert checkpoint["sub_tasks"][0]["status"] == "completed"
    assert checkpoint["sub_tasks"][0]["attempts"] == 1
    event_types = [event["event_type"] for event in store.list_task_events("task_fixed")]
    assert "planned" in event_types
    assert "subtask_running" in event_types
    assert "subtask_completed" in event_types
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_cancel_marks_task_cancelled(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    orchestrator = SlowOrchestrator()
    runner = TaskJobRunner(orchestrator, None, store, {})

    runner.submit(TaskJobRequest(description="长任务", task_id="task_fixed"))
    await asyncio.sleep(0)

    assert runner.cancel("task_fixed") is True
    await _wait_until_done(runner)

    persisted = store.get_task("task_fixed")
    assert persisted["status"] == "cancelled"
    assert persisted["result"] == "(用户已取消任务)"
    assert "task_fixed" in orchestrator.cancelled
    events = store.list_task_events("task_fixed")
    assert any(event["event_type"] == "cancel_requested" for event in events)
    cancelled_event = [event for event in events if event["event_type"] == "cancelled"][-1]
    assert cancelled_event["details"]["failure_type"] == "user_cancelled"
    assert cancelled_event["details"]["retryable"] is False
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_immediate_cancel_persists_cancelled(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    runner = TaskJobRunner(SlowOrchestrator(), None, store, {})

    runner.submit(TaskJobRequest(description="长任务", task_id="task_fixed"))

    assert runner.cancel("task_fixed") is True
    await _wait_until_done(runner)
    assert store.get_task("task_fixed")["status"] == "cancelled"
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_timeout_records_retryable_failure(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    runner = TaskJobRunner(SlowOrchestrator(), None, store, {}, owner_id="runner_timeout")

    runner.submit(TaskJobRequest(description="长任务", task_id="task_fixed", timeout_seconds=0.01))
    await _wait_until_done(runner)

    persisted = store.get_task("task_fixed")
    assert persisted["status"] == "timeout"
    assert "可重试" in persisted["result"]
    timeout_event = [event for event in store.list_task_events("task_fixed") if event["event_type"] == "timeout"][-1]
    assert timeout_event["details"]["failure_type"] == "timeout"
    assert timeout_event["details"]["retryable"] is True
    assert timeout_event["details"]["timeout_seconds"] == 0.01
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_exception_failure_is_classified(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    runner = TaskJobRunner(ErrorOrchestrator(ValueError("bad request")), None, store, {}, owner_id="runner_fail")

    runner.submit(TaskJobRequest(description="失败任务", task_id="task_fixed"))
    await _wait_until_done(runner)

    persisted = store.get_task("task_fixed")
    assert persisted["status"] == "failed"
    assert "不可重试" in persisted["result"]
    failed_event = [
        event for event in store.list_task_events("task_fixed")
        if event["event_type"] == "failed_non_retryable"
    ][-1]
    assert failed_event["details"]["failure_type"] == "non_retryable_exception"
    assert failed_event["details"]["retryable"] is False
    assert failed_event["details"]["error_type"] == "ValueError"
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_retryable_exception_is_classified(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    runner = TaskJobRunner(ErrorOrchestrator(OSError("network unavailable")), None, store, {}, owner_id="runner_fail")

    runner.submit(TaskJobRequest(description="失败任务", task_id="task_fixed"))
    await _wait_until_done(runner)

    persisted = store.get_task("task_fixed")
    assert persisted["status"] == "failed"
    assert "可重试" in persisted["result"]
    failed_event = [
        event for event in store.list_task_events("task_fixed")
        if event["event_type"] == "failed_retryable"
    ][-1]
    assert failed_event["details"]["failure_type"] == "retryable_exception"
    assert failed_event["details"]["retryable"] is True
    assert failed_event["details"]["error_type"] == "OSError"
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_persists_failed_orchestrator_result(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    runner = TaskJobRunner(FailedResultOrchestrator(), None, store, {}, owner_id="runner_fail")

    runner.submit(TaskJobRequest(description="失败结果", task_id="task_fixed"))
    await _wait_until_done(runner)

    persisted = store.get_task("task_fixed")
    assert persisted["status"] == "failed"
    assert persisted["final_score"] == 0.0
    assert "ConnectError" in persisted["result"]
    failed_event = [
        event for event in store.list_task_events("task_fixed")
        if event["event_type"] == "failed_retryable"
    ][-1]
    assert failed_event["details"]["failure_type"] == "retryable_exception"
    assert failed_event["details"]["retryable"] is True
    assert failed_event["details"]["error_type"] == "OrchestratorFailed"
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_lease_loss_stops_without_terminal_overwrite(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    slow = SlowOrchestrator()
    runner = TaskJobRunner(
        slow,
        None,
        store,
        {},
        owner_id="runner_a",
        lease_refresh_interval_seconds=0.1,
    )

    runner.submit(TaskJobRequest(description="长任务", task_id="task_fixed"))
    await asyncio.sleep(0)
    assert store.release_task_job_lease("task_fixed", "runner_a") is True
    await _wait_until_done(runner)

    persisted = store.get_task("task_fixed")
    assert persisted["status"] == "running"
    events = store.list_task_events("task_fixed")
    lease_event = [event for event in events if event["event_type"] == "lease_lost"][-1]
    assert lease_event["details"]["failure_type"] == "lease_lost"
    assert lease_event["details"]["retryable"] is True
    assert any(event["event_type"] == "lease_lost_stopped" for event in events)
    assert not any(event["event_type"] == "cancelled" for event in events)
    assert "task_fixed" in slow.cancelled

    recover_runner = TaskJobRunner(FastOrchestrator(), None, store, {}, owner_id="runner_b")
    assert recover_runner.recover_pending() == ["task_fixed"]
    await _wait_until_done(recover_runner)
    assert store.get_task("task_fixed")["status"] == "completed"
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_retries_timeout_from_checkpoint(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    created_at = "2026-05-21T10:00:00"
    store.save_task({
        "task_id": "task_fixed",
        "description": "重试任务",
        "status": "timeout",
        "result": "任务执行超时（1秒，可重试）",
        "created_at": created_at,
    })
    store.save_task_job_request(
        "task_fixed",
        "重试任务",
        context={"source": "test"},
        timeout_seconds=30,
        created_at=created_at,
    )
    store.save_task_checkpoint(
        "task_fixed",
        {
            "task_id": "task_fixed",
            "description": "重试任务",
            "status": "running",
            "sub_tasks": [
                {
                    "id": "sub_done",
                    "description": "已完成子任务",
                    "status": "completed",
                    "result": "ok",
                    "attempts": 1,
                }
            ],
        },
    )
    store.add_task_event("task_fixed", "timeout", "任务执行超时（1秒）", {
        "failure_type": "timeout",
        "retryable": True,
    })
    orchestrator = FastOrchestrator()
    runner = TaskJobRunner(orchestrator, None, store, {}, owner_id="runner_retry")

    queued = runner.retry("task_fixed")
    await _wait_until_done(runner)

    assert queued["task_id"] == "task_fixed"
    assert queued["status"] == "queued"
    assert orchestrator.context["source"] == "test"
    assert orchestrator.context["_resume_checkpoint"]["task_id"] == "task_fixed"
    persisted = store.get_task("task_fixed")
    assert persisted["status"] == "completed"
    assert persisted["result"] == "完成: 重试任务"
    events = store.list_task_events("task_fixed")
    retry_event = [event for event in events if event["event_type"] == "retry_queued"][-1]
    assert retry_event["details"]["previous_status"] == "timeout"
    assert retry_event["details"]["previous_failure_type"] == "timeout"
    assert retry_event["details"]["resumed_from_checkpoint"] is True
    request = store.get_task_job_request("task_fixed")
    assert request["recovery_count"] == 1
    assert request["lease_owner"] == ""
    store.close()


def test_task_job_runner_rejects_non_retryable_failure(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    store.save_task({
        "task_id": "task_fixed",
        "description": "不可重试任务",
        "status": "failed",
        "result": "任务执行失败（不可重试）",
    })
    store.save_task_job_request("task_fixed", "不可重试任务")
    store.add_task_event("task_fixed", "failed_non_retryable", "任务执行失败（不可重试）", {
        "failure_type": "non_retryable_exception",
        "retryable": False,
    })
    runner = TaskJobRunner(FastOrchestrator(), None, store, {}, owner_id="runner_retry")

    with pytest.raises(ValueError, match="not marked retryable"):
        runner.retry("task_fixed")
    assert runner.list_running() == []
    assert store.get_task("task_fixed")["status"] == "failed"
    store.close()


def test_task_job_runner_recovery_rejects_active_foreign_lease(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    store.save_task({
        "task_id": "task_fixed",
        "description": "运行任务",
        "status": "running",
    })
    store.save_task_job_request("task_fixed", "运行任务")
    assert store.acquire_task_job_lease("task_fixed", "runner_a", 60)
    runner = TaskJobRunner(FastOrchestrator(), None, store, {}, owner_id="runner_b")

    with pytest.raises(RuntimeError, match="leased by another runner"):
        runner.retry("task_fixed")
    assert runner.list_running() == []
    assert store.get_task_job_request("task_fixed")["lease_owner"] == "runner_a"
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_auto_retries_retryable_failure(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    orchestrator = FlakyOrchestrator()
    runner = TaskJobRunner(
        orchestrator,
        None,
        store,
        {},
        owner_id="runner_auto",
        auto_retry_enabled=True,
        auto_retry_max_attempts=1,
        auto_retry_initial_delay_seconds=0,
        auto_retry_max_delay_seconds=0,
    )

    runner.submit(TaskJobRequest(description="自动重试任务", task_id="task_fixed"))
    persisted = await _wait_for_task_status(store, "task_fixed", "completed")

    assert persisted["result"] == "完成: 自动重试任务"
    assert orchestrator.calls == 2
    events = store.list_task_events("task_fixed")
    event_types = [event["event_type"] for event in events]
    assert "failed_retryable" in event_types
    assert "auto_retry_scheduled" in event_types
    assert "auto_retry_queued" in event_types
    assert event_types[-1] == "completed"
    request = store.get_task_job_request("task_fixed")
    assert request["auto_retry_count"] == 1
    assert request["next_retry_at"] == ""
    assert request["recovery_count"] == 1
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_recover_pending_reschedules_future_auto_retry(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    next_retry_at = (datetime.now() - timedelta(seconds=1)).isoformat()
    store.save_task({
        "task_id": "task_fixed",
        "description": "恢复自动重试",
        "status": "timeout",
        "result": "任务执行超时（1秒，可重试）",
        "created_at": "2026-05-21T09:59:00",
    })
    store.save_task_job_request("task_fixed", "恢复自动重试", created_at="2026-05-21T09:59:00")
    store.add_task_event("task_fixed", "timeout", "任务执行超时（1秒）", {
        "failure_type": "timeout",
        "retryable": True,
    })
    scheduled = store.schedule_task_job_auto_retry("task_fixed", next_retry_at, max_attempts=2)
    assert scheduled["next_retry_at"] == next_retry_at

    runner = TaskJobRunner(
        FastOrchestrator(),
        None,
        store,
        {},
        owner_id="runner_auto_recover",
        auto_retry_enabled=True,
        auto_retry_max_attempts=2,
        auto_retry_initial_delay_seconds=0,
        auto_retry_max_delay_seconds=0,
    )
    assert runner.recover_pending() == []
    persisted = await _wait_for_task_status(store, "task_fixed", "completed")

    assert persisted["result"] == "完成: 恢复自动重试"
    events = store.list_task_events("task_fixed")
    assert any(event["event_type"] == "auto_retry_queued" for event in events)
    assert store.get_task_job_request("task_fixed")["next_retry_at"] == ""
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_recovers_pending_jobs(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    created_at = "2026-05-21T10:00:00"
    store.save_task({"task_id": "task_fixed", "description": "恢复任务", "status": "running", "created_at": created_at})
    store.save_task_job_request("task_fixed", "恢复任务", context={"source": "test"}, created_at=created_at)
    store.save_task_checkpoint(
        "task_fixed",
        {
            "task_id": "task_fixed",
            "status": "running",
            "sub_tasks": [
                {
                    "id": "sub_done",
                    "description": "已完成子任务",
                    "status": "completed",
                    "result": "ok",
                    "attempts": 1,
                }
            ],
        },
    )

    orchestrator = FastOrchestrator()
    runner = TaskJobRunner(orchestrator, None, store, {})

    recovered = runner.recover_pending()
    await _wait_until_done(runner)

    assert recovered == ["task_fixed"]
    assert orchestrator.context["_resume_checkpoint"]["task_id"] == "task_fixed"
    assert orchestrator.context["source"] == "test"
    request = store.get_task_job_request("task_fixed")
    assert request["recovery_count"] == 1
    assert request["last_recovered_at"]
    persisted = store.get_task("task_fixed")
    assert persisted["status"] == "completed"
    assert persisted["created_at"] == created_at
    event_types = [event["event_type"] for event in store.list_task_events("task_fixed")]
    assert "recovered" in event_types
    assert event_types[-1] == "completed"
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_recovery_skips_jobs_with_active_foreign_lease(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    created_at = "2026-05-21T10:00:00"
    store.save_task({"task_id": "task_fixed", "description": "恢复任务", "status": "running", "created_at": created_at})
    store.save_task_job_request("task_fixed", "恢复任务", context={"source": "test"}, created_at=created_at)
    assert store.acquire_task_job_lease("task_fixed", "runner_a", 60)

    runner = TaskJobRunner(FastOrchestrator(), None, store, {}, owner_id="runner_b")

    assert runner.recover_pending() == []
    assert runner.list_running() == []
    request = store.get_task_job_request("task_fixed")
    assert request["lease_owner"] == "runner_a"
    stats = store.get_task_job_stats()
    assert stats["active"] == 1
    assert stats["leased_active"] == 1
    assert stats["expired_leases"] == 0
    assert store.list_task_events("task_fixed") == []
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_recovery_claims_expired_foreign_lease(tmp_path):
    store = PersistenceStore(tmp_path / "jobs.db")
    created_at = "2026-05-21T10:00:00"
    store.save_task({"task_id": "task_fixed", "description": "恢复任务", "status": "running", "created_at": created_at})
    store.save_task_job_request("task_fixed", "恢复任务", context={"source": "test"}, created_at=created_at)
    assert store.acquire_task_job_lease("task_fixed", "runner_a", -1)

    orchestrator = FastOrchestrator()
    runner = TaskJobRunner(orchestrator, None, store, {}, owner_id="runner_b")

    assert runner.recover_pending() == ["task_fixed"]
    await _wait_until_done(runner)

    assert orchestrator.context["source"] == "test"
    persisted = store.get_task("task_fixed")
    assert persisted["status"] == "completed"
    request = store.get_task_job_request("task_fixed")
    assert request["lease_owner"] == ""
    assert request["lease_expires_at"] == ""
    assert request["recovery_count"] == 1
    assert request["last_recovered_at"]
    store.close()


@pytest.mark.asyncio
async def test_task_job_runner_resume_after_process_restart_skips_completed_subtasks(tmp_path):
    from coordinator.iterative_orchestrator import IterativeOrchestrator

    db_path = tmp_path / "jobs.db"
    created_at = "2026-05-21T10:00:00"

    old_store = PersistenceStore(db_path)
    old_store.save_task({
        "task_id": "task_restart",
        "description": "恢复真实编排任务",
        "status": "running",
        "created_at": created_at,
    })
    old_store.save_task_job_request(
        "task_restart",
        "恢复真实编排任务",
        context={"source": "restart-test"},
        created_at=created_at,
    )
    old_store.save_task_checkpoint(
        "task_restart",
        {
            "task_id": "task_restart",
            "description": "恢复真实编排任务",
            "status": "running",
            "sub_tasks": [
                {
                    "id": "sub_done",
                    "description": "已完成的调研",
                    "status": "completed",
                    "result": "调研结果",
                    "attempts": 1,
                },
                {
                    "id": "sub_resume",
                    "description": "继续生成报告",
                    "dependencies": ["sub_done"],
                    "status": "running",
                    "attempts": 0,
                },
            ],
        },
    )
    old_store.close()

    class ResumeWorkerPool:
        def __init__(self):
            self.executed = []
            self.contexts = {}

        def get_worker_for(self, subtask):
            return None

        async def execute_parallel(self, tasks, context=None, on_progress=None, per_task_contexts=None):
            self.executed.extend(task.id for task in tasks)
            self.contexts.update(per_task_contexts or {})
            return [(task, f"续跑完成: {task.id}", None) for task in tasks]

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=MagicMock(
        content=json.dumps({"score": 0.95, "passed": True, "improvements": []})
    ))
    planner = MagicMock()
    planner.plan_task = AsyncMock(side_effect=AssertionError("checkpoint recovery must not replan"))
    worker_pool = ResumeWorkerPool()
    store = PersistenceStore(db_path)
    orchestrator = IterativeOrchestrator(
        planner=planner,
        worker_pool=worker_pool,
        provider=provider,
        rag_engine=None,
        model="test-model",
        base_workspace=tmp_path / "workspace",
    )
    runner = TaskJobRunner(orchestrator, None, store, {})

    recovered = runner.recover_pending()
    await _wait_until_done(runner)

    assert recovered == ["task_restart"]
    assert worker_pool.executed == ["sub_resume"]
    assert worker_pool.contexts["sub_resume"]["dependency_results"] == {"sub_done": "调研结果"}
    assert worker_pool.contexts["sub_resume"]["source"] == "restart-test"
    planner.plan_task.assert_not_called()

    persisted = store.get_task("task_restart")
    assert persisted["status"] == "completed"
    assert "调研结果" in persisted["result"]
    assert "续跑完成: sub_resume" in persisted["result"]
    assert persisted["created_at"] == created_at

    checkpoint = store.get_task_checkpoint("task_restart")
    assert [subtask["status"] for subtask in checkpoint["sub_tasks"]] == ["completed", "completed"]
    assert checkpoint["sub_tasks"][0]["attempts"] == 1
    assert checkpoint["sub_tasks"][1]["attempts"] == 1

    events = store.list_task_events("task_restart")
    planned_events = [event for event in events if event["event_type"] == "planned"]
    assert planned_events[-1]["details"]["resumed_from_checkpoint"] is True
    assert any(event["event_type"] == "subtask_completed" for event in events)
    assert events[-1]["event_type"] == "completed"
    job = store.get_task_job_request("task_restart")
    assert job["recovery_count"] == 1
    assert job["last_recovered_at"]
    store.close()
