"""Background task job runner with persistent task history updates."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from agents.worker_pool import Task
from coordinator.iterative_orchestrator import IterationResult, IterativeOrchestrator
from coordinator.task_planner import TaskPlanner
from storage.persistence import PersistenceStore

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "timeout"}
DEFAULT_LEASE_SECONDS = 60.0
FAILURE_USER_CANCELLED = "user_cancelled"
FAILURE_ORCHESTRATOR_CANCELLED = "orchestrator_cancelled"
FAILURE_ORCHESTRATOR_FAILED = "orchestrator_failed"
FAILURE_LEASE_LOST = "lease_lost"
FAILURE_TIMEOUT = "timeout"
FAILURE_RETRYABLE_EXCEPTION = "retryable_exception"
FAILURE_NON_RETRYABLE_EXCEPTION = "non_retryable_exception"
RETRYABLE_FAILURE_TYPES = {
    FAILURE_LEASE_LOST,
    FAILURE_TIMEOUT,
    FAILURE_RETRYABLE_EXCEPTION,
}


@dataclass
class TaskJobRequest:
    """A task submission that can run beyond the HTTP request lifecycle."""

    description: str
    context: dict[str, Any] = field(default_factory=dict)
    generate_suggestions: bool = True
    active_group_ids: list[str] | None = None
    timeout_seconds: int | None = None
    task_id: str | None = None
    created_at: str | None = None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> TaskJobRequest:
        return cls(
            description=record.get("description", ""),
            context=record.get("context") or {},
            generate_suggestions=bool(record.get("generate_suggestions", True)),
            active_group_ids=record.get("active_group_ids"),
            timeout_seconds=record.get("timeout_seconds"),
            task_id=record.get("task_id"),
            created_at=record.get("created_at"),
        )


class TaskJobRunner:
    """Submit task execution as in-process background jobs and persist state."""

    def __init__(
        self,
        orchestrator: IterativeOrchestrator,
        task_planner: TaskPlanner | None,
        store: PersistenceStore,
        result_cache: dict[str, dict[str, Any]] | None = None,
        owner_id: str | None = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        lease_refresh_interval_seconds: float | None = None,
        auto_retry_enabled: bool = False,
        auto_retry_max_attempts: int = 0,
        auto_retry_initial_delay_seconds: float = 30.0,
        auto_retry_max_delay_seconds: float = 300.0,
        auto_retry_backoff_multiplier: float = 2.0,
    ):
        self._orchestrator = orchestrator
        self._task_planner = task_planner
        self._store = store
        self._result_cache = result_cache if result_cache is not None else {}
        self._jobs: dict[str, asyncio.Task] = {}
        self._scheduled_retries: dict[str, asyncio.Task] = {}
        self._terminal_reasons: dict[str, dict[str, Any]] = {}
        self._owner_id = owner_id or f"runner_{uuid.uuid4().hex[:12]}"
        self._lease_seconds = max(1.0, float(lease_seconds))
        refresh_interval = lease_refresh_interval_seconds
        if refresh_interval is None:
            refresh_interval = min(20.0, max(1.0, self._lease_seconds / 3))
        self._lease_refresh_interval_seconds = max(0.1, float(refresh_interval))
        self._auto_retry_enabled = bool(auto_retry_enabled)
        self._auto_retry_max_attempts = max(0, int(auto_retry_max_attempts))
        self._auto_retry_initial_delay_seconds = max(0.0, float(auto_retry_initial_delay_seconds))
        self._auto_retry_max_delay_seconds = max(0.0, float(auto_retry_max_delay_seconds))
        self._auto_retry_backoff_multiplier = max(1.0, float(auto_retry_backoff_multiplier))

    def submit(self, request: TaskJobRequest) -> dict[str, Any]:
        """Persist a queued task and start its execution in the background."""
        task_id = request.task_id or f"task_{uuid.uuid4().hex[:8]}"
        created_at = request.created_at or datetime.now().isoformat()
        accepted = self._task_payload(
            task_id=task_id,
            description=request.description,
            status="queued",
            result="",
            created_at=created_at,
        )
        self._store.save_task_job_request(
            task_id=task_id,
            description=request.description,
            context=request.context,
            generate_suggestions=request.generate_suggestions,
            active_group_ids=request.active_group_ids,
            timeout_seconds=request.timeout_seconds,
            created_at=created_at,
        )
        if not self._store.acquire_task_job_lease(task_id, self._owner_id, self._lease_seconds):
            self._store.add_task_event(
                task_id,
                "lease_rejected",
                "任务已被其他执行器持有，无法重复提交",
                {"owner_id": self._owner_id},
                created_at=created_at,
            )
            raise RuntimeError(f"task job {task_id} is already leased by another runner")
        self._store.save_task(accepted)
        self._store.add_task_event(
            task_id,
            "queued",
            "任务已进入后台执行队列",
            {"recoverable": True},
            created_at=created_at,
        )
        self._result_cache[task_id] = accepted

        job_request = TaskJobRequest(
            description=request.description,
            context=dict(request.context or {}),
            generate_suggestions=request.generate_suggestions,
            active_group_ids=request.active_group_ids,
            timeout_seconds=request.timeout_seconds,
            task_id=task_id,
            created_at=created_at,
        )
        self._start(job_request, created_at)
        return accepted

    def recover_pending(self, limit: int = 100) -> list[str]:
        """Restart recoverable queued/running jobs after service startup."""
        recovered: list[str] = []
        for record in self._store.list_recoverable_task_jobs(limit=limit):
            request = TaskJobRequest.from_record(record)
            if not request.task_id or request.task_id in self._jobs:
                continue
            created_at = request.created_at or datetime.now().isoformat()
            if not self._store.acquire_task_job_lease(request.task_id, self._owner_id, self._lease_seconds):
                continue
            recovered_record = self._store.mark_task_job_recovered(request.task_id) or record
            checkpoint = self._store.get_task_checkpoint(request.task_id)
            if checkpoint:
                request.context = {
                    **(request.context or {}),
                    "_resume_checkpoint": checkpoint,
                }
            self._store.add_task_event(
                request.task_id,
                "recovered",
                "服务启动时恢复未完成任务，已重新入队",
                {
                    "resumed_from_checkpoint": bool(checkpoint),
                    "subtask_count": len(checkpoint.get("sub_tasks", [])) if checkpoint else 0,
                    "owner_id": self._owner_id,
                    "recovery_count": recovered_record.get("recovery_count", 0),
                    "last_recovered_at": recovered_record.get("last_recovered_at", ""),
                },
            )
            self._save(
                task_id=request.task_id,
                description=request.description,
                status="queued",
                result="",
                created_at=created_at,
                completed_at=None,
            )
            self._start(request, created_at)
            recovered.append(request.task_id)
        self._schedule_existing_auto_retries(limit=limit)
        return recovered

    def list_running(self) -> list[str]:
        """Return job ids that are active in this process."""
        return sorted(task_id for task_id, job in self._jobs.items() if not job.done())

    def cancel(self, task_id: str) -> bool:
        """Cancel an active background job, or mark a queued/running persisted job cancelled."""
        cancelled = self._orchestrator.cancel_task(task_id)
        job = self._jobs.get(task_id)
        if job and not job.done():
            self._remember_terminal_reason(
                task_id,
                FAILURE_USER_CANCELLED,
                retryable=False,
                message="用户请求取消任务",
            )
            job.cancel()
            cancelled = True
        if cancelled:
            persisted = self._store.get_task(task_id)
            if persisted:
                payload = {**persisted, "task_id": task_id, "status": "cancelled", "result": "(用户已取消任务)"}
                self._store.save_task(payload)
                self._result_cache[task_id] = payload
            self._store.add_task_event(
                task_id,
                "cancel_requested",
                "任务已收到用户取消请求",
                {
                    "failure_type": FAILURE_USER_CANCELLED,
                    "retryable": False,
                    "owner_id": self._owner_id,
                },
            )
            self._store.release_task_job_lease(task_id, self._owner_id)
            return True

        persisted = self._store.get_task(task_id)
        if persisted and persisted.get("status") in {"queued", "pending", "running"}:
            self._remember_terminal_reason(
                task_id,
                FAILURE_USER_CANCELLED,
                retryable=False,
                message="用户请求取消任务",
            )
            payload = {**persisted, "task_id": task_id, "status": "cancelled", "result": "(用户已取消任务)"}
            self._store.save_task(payload)
            self._result_cache[task_id] = payload
            self._store.add_task_event(
                task_id,
                "cancelled",
                "任务已由用户取消",
                {
                    "failure_type": FAILURE_USER_CANCELLED,
                    "retryable": False,
                    "owner_id": self._owner_id,
                },
            )
            self._store.release_task_job_lease(task_id, self._owner_id)
            return True
        return False

    def retry(self, task_id: str, force: bool = False, *, auto: bool = False) -> dict[str, Any]:
        """Requeue a retryable failed job, or manually recover a stale queued/running job."""
        job = self._jobs.get(task_id)
        if job and not job.done():
            raise RuntimeError(f"task job {task_id} is already running in this process")

        request_record = self._store.get_task_job_request(task_id)
        persisted = self._store.get_task(task_id)
        if not request_record or not persisted:
            raise LookupError(f"task job {task_id} not found")

        status = str(persisted.get("status") or "")
        latest_failure = self._latest_failure(task_id)
        active_statuses = {"queued", "pending", "running"}
        retryable = self._is_retryable_for_manual_retry(status, latest_failure)

        if status == "completed":
            raise ValueError("completed tasks cannot be retried")
        if status == "cancelled" and not force:
            raise ValueError("cancelled tasks require force retry")
        if status not in active_statuses and not force and not retryable:
            raise ValueError("task failure is not marked retryable")

        self._cancel_scheduled_retry(task_id)

        request = TaskJobRequest.from_record(request_record)
        request.created_at = request.created_at or persisted.get("created_at")
        created_at = request.created_at or datetime.now().isoformat()
        checkpoint = self._store.get_task_checkpoint(task_id)
        request.context = dict(request.context or {})
        if checkpoint:
            request.context["_resume_checkpoint"] = checkpoint

        if not self._store.acquire_task_job_lease(task_id, self._owner_id, self._lease_seconds):
            raise RuntimeError(f"task job {task_id} is leased by another runner")

        self._store.clear_task_job_auto_retry(task_id)
        recovered_record = self._store.mark_task_job_recovered(task_id) or request_record
        event_type = "auto_retry_queued" if auto else ("recovery_queued" if status in active_statuses else "retry_queued")
        message = "任务已自动重试入队" if auto else ("任务已手动恢复入队" if status in active_statuses else "任务已重新入队")
        details = {
            "previous_status": status,
            "previous_failure_type": latest_failure.get("failure_type", ""),
            "retryable": retryable or force,
            "force": force,
            "auto": auto,
            "resumed_from_checkpoint": bool(checkpoint),
            "owner_id": self._owner_id,
            "recovery_count": recovered_record.get("recovery_count", 0),
        }
        self._store.add_task_event(task_id, event_type, message, details)
        self._save(
            task_id=task_id,
            description=request.description,
            status="queued",
            result="",
            created_at=created_at,
            completed_at=None,
        )
        self._start(request, created_at)
        return self._result_cache[task_id]

    def _start(self, request: TaskJobRequest, created_at: str) -> None:
        task_id = request.task_id or f"task_{uuid.uuid4().hex[:8]}"
        job = asyncio.create_task(self._run(request, created_at), name=f"task_job:{task_id}")
        self._jobs[task_id] = job
        job.add_done_callback(lambda _done, tid=task_id: self._jobs.pop(tid, None))

    async def _run(self, request: TaskJobRequest, created_at: str) -> None:
        task_id = request.task_id or f"task_{uuid.uuid4().hex[:8]}"
        self._store.add_task_event(task_id, "running", "任务开始执行")
        self._save(
            task_id=task_id,
            description=request.description,
            status="running",
            result="",
            created_at=created_at,
            completed_at=None,
        )
        heartbeat = asyncio.create_task(
            self._refresh_lease_until_done(task_id),
            name=f"task_job_lease:{task_id}",
        )

        try:
            result = await self._execute_orchestrator(request)
            status = result.status if result.status in TERMINAL_STATUSES else "completed"
            if result.result_summary == "(任务已取消)":
                status = "cancelled"
            reason = None
            if status == "cancelled":
                reason = self._pop_terminal_reason(
                    task_id,
                    default_failure_type=FAILURE_ORCHESTRATOR_CANCELLED,
                    default_retryable=False,
                    default_message="编排器返回取消状态",
                )
            elif status == "failed":
                failure_type, retryable = self._classify_failure_text(result.error or result.result_summary)
                reason = {
                    "failure_type": failure_type,
                    "retryable": retryable,
                    "error_type": "OrchestratorFailed",
                    "error": result.error or result.result_summary,
                    "owner_id": self._owner_id,
                }
            suggestions = []
            if status == "completed" and request.generate_suggestions and self._task_planner:
                placeholder_task = Task(
                    id=result.task_id,
                    description=request.description,
                )
                suggestions = await self._task_planner.generate_optimization_suggestions(
                    placeholder_task,
                    result.result_summary,
                    request.context,
                )
            mail_log = self._read_mail_log(result.shared_dir)
            payload = {
                "task_id": result.task_id,
                "description": request.description,
                "status": status,
                "result": result.result_summary,
                "shared_dir": result.shared_dir,
                "final_score": result.final_score,
                "iterations": [
                    {
                        "iteration": record.iteration,
                        "score": record.score,
                        "improvements": record.improvements,
                    }
                    for record in result.iterations
                ],
                "mail_log": mail_log,
                "suggestions": [
                    {
                        "type": suggestion.type,
                        "title": suggestion.title,
                        "description": suggestion.description,
                        "confidence": suggestion.confidence,
                        "code_snippet": suggestion.code_snippet,
                        "priority": suggestion.priority,
                    }
                    for suggestion in suggestions
                ],
                "created_at": created_at,
            }
            self._store.save_task(payload)
            self._store.add_task_event(
                result.task_id,
                self._terminal_event_type(status, reason),
                self._terminal_result_message(status, reason),
                {
                    "final_score": result.final_score,
                    "iterations": len(result.iterations),
                    **(reason or {}),
                },
            )
            self._store.clear_task_job_auto_retry(result.task_id)
            self._result_cache[result.task_id] = payload
            if status == "failed" and reason:
                self._schedule_auto_retry_if_needed(task_id, reason.get("failure_type", ""))
        except asyncio.TimeoutError:
            logger.warning(f"[TaskJobRunner] 任务 {task_id} 执行超时")
            details = {
                "failure_type": FAILURE_TIMEOUT,
                "retryable": True,
                "timeout_seconds": request.timeout_seconds,
                "owner_id": self._owner_id,
            }
            self._save_terminal_error(
                task_id=task_id,
                description=request.description,
                status="timeout",
                result=f"任务执行超时（{request.timeout_seconds}秒，可重试）",
                created_at=created_at,
            )
            self._store.add_task_event(task_id, "timeout", f"任务执行超时（{request.timeout_seconds}秒）", details)
            self._schedule_auto_retry_if_needed(task_id, FAILURE_TIMEOUT)
        except asyncio.CancelledError:
            reason = self._pop_terminal_reason(
                task_id,
                default_failure_type=FAILURE_ORCHESTRATOR_CANCELLED,
                default_retryable=False,
                default_message="任务已取消",
            )
            if reason["failure_type"] == FAILURE_LEASE_LOST:
                logger.warning(f"[TaskJobRunner] 任务 {task_id} 因租约丢失停止本地执行")
                self._store.add_task_event(task_id, "lease_lost_stopped", reason["message"], reason)
            else:
                logger.info(f"[TaskJobRunner] 任务 {task_id} 已取消: {reason['failure_type']}")
                self._save_terminal_error(
                    task_id=task_id,
                    description=request.description,
                    status="cancelled",
                    result="(用户已取消任务)" if reason["failure_type"] == FAILURE_USER_CANCELLED else "(任务已取消)",
                    created_at=created_at,
                )
                self._store.add_task_event(task_id, "cancelled", reason["message"], reason)
                self._store.clear_task_job_auto_retry(task_id)
        except Exception as exc:
            logger.exception(f"[TaskJobRunner] 任务 {task_id} 执行失败")
            failure_type, retryable = self._classify_exception(exc)
            event_type = "failed_retryable" if retryable else "failed_non_retryable"
            message = "任务执行失败（可重试）" if retryable else "任务执行失败（不可重试）"
            self._save_terminal_error(
                task_id=task_id,
                description=request.description,
                status="failed",
                result=f"{message}: {type(exc).__name__}: {exc}",
                created_at=created_at,
            )
            self._store.add_task_event(
                task_id,
                event_type,
                message,
                {
                    "failure_type": failure_type,
                    "retryable": retryable,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "owner_id": self._owner_id,
                },
            )
            self._schedule_auto_retry_if_needed(task_id, failure_type)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            self._store.release_task_job_lease(task_id, self._owner_id)

    async def _refresh_lease_until_done(self, task_id: str) -> None:
        while True:
            await asyncio.sleep(self._lease_refresh_interval_seconds)
            refreshed = self._store.refresh_task_job_lease(task_id, self._owner_id, self._lease_seconds)
            if refreshed:
                continue
            logger.error(f"[TaskJobRunner] 任务 {task_id} 租约刷新失败，取消本地执行以避免重复运行")
            job = self._jobs.get(task_id)
            if job and not job.done():
                reason = self._remember_terminal_reason(
                    task_id,
                    FAILURE_LEASE_LOST,
                    retryable=True,
                    message="执行租约已丢失，本地执行已停止以避免重复运行",
                )
                self._store.add_task_event(task_id, "lease_lost", reason["message"], reason)
                self._orchestrator.cancel_task(task_id)
                job.cancel()
            return

    def _remember_terminal_reason(
        self,
        task_id: str,
        failure_type: str,
        *,
        retryable: bool,
        message: str,
    ) -> dict[str, Any]:
        reason = {
            "failure_type": failure_type,
            "retryable": retryable,
            "message": message,
            "owner_id": self._owner_id,
        }
        self._terminal_reasons[task_id] = reason
        return reason

    def _pop_terminal_reason(
        self,
        task_id: str,
        *,
        default_failure_type: str,
        default_retryable: bool,
        default_message: str,
    ) -> dict[str, Any]:
        return self._terminal_reasons.pop(
            task_id,
            {
                "failure_type": default_failure_type,
                "retryable": default_retryable,
                "message": default_message,
                "owner_id": self._owner_id,
            },
        )

    def _schedule_auto_retry_if_needed(self, task_id: str, failure_type: str) -> None:
        if not self._auto_retry_enabled or self._auto_retry_max_attempts <= 0:
            self._store.clear_task_job_auto_retry(task_id)
            return
        if failure_type not in RETRYABLE_FAILURE_TYPES:
            self._store.clear_task_job_auto_retry(task_id)
            return

        request_record = self._store.get_task_job_request(task_id)
        if not request_record:
            return
        used_attempts = int(request_record.get("auto_retry_count") or 0)
        if used_attempts >= self._auto_retry_max_attempts:
            self._store.clear_task_job_auto_retry(task_id)
            self._store.add_task_event(
                task_id,
                "auto_retry_exhausted",
                "自动重试次数已用尽",
                {
                    "failure_type": failure_type,
                    "auto_retry_count": used_attempts,
                    "max_attempts": self._auto_retry_max_attempts,
                    "owner_id": self._owner_id,
                },
            )
            return

        delay_seconds = self._auto_retry_delay_seconds(used_attempts)
        next_retry_at = (datetime.now() + timedelta(seconds=delay_seconds)).isoformat()
        scheduled = self._store.schedule_task_job_auto_retry(
            task_id,
            next_retry_at,
            self._auto_retry_max_attempts,
        )
        if not scheduled:
            self._store.add_task_event(
                task_id,
                "auto_retry_exhausted",
                "自动重试次数已用尽",
                {
                    "failure_type": failure_type,
                    "auto_retry_count": used_attempts,
                    "max_attempts": self._auto_retry_max_attempts,
                    "owner_id": self._owner_id,
                },
            )
            return

        self._store.add_task_event(
            task_id,
            "auto_retry_scheduled",
            "任务已安排自动重试",
            {
                "failure_type": failure_type,
                "auto_retry_count": scheduled.get("auto_retry_count", 0),
                "max_attempts": self._auto_retry_max_attempts,
                "delay_seconds": delay_seconds,
                "next_retry_at": next_retry_at,
                "owner_id": self._owner_id,
            },
        )
        self._schedule_delayed_auto_retry(task_id, next_retry_at)

    def _schedule_existing_auto_retries(self, limit: int = 100) -> None:
        if not self._auto_retry_enabled or self._auto_retry_max_attempts <= 0:
            return
        for record in self._store.list_scheduled_task_job_retries(limit=limit):
            task_id = record.get("task_id")
            next_retry_at = record.get("next_retry_at")
            if task_id and next_retry_at and task_id not in self._jobs:
                self._schedule_delayed_auto_retry(task_id, next_retry_at)

    def _schedule_delayed_auto_retry(self, task_id: str, next_retry_at: str) -> None:
        self._cancel_scheduled_retry(task_id)
        retry_at = self._parse_datetime(next_retry_at)
        delay_seconds = max(0.0, (retry_at - datetime.now()).total_seconds()) if retry_at else 0.0
        retry_task = asyncio.create_task(
            self._delayed_auto_retry(task_id, next_retry_at, delay_seconds),
            name=f"task_job_auto_retry:{task_id}",
        )
        self._scheduled_retries[task_id] = retry_task

    async def _delayed_auto_retry(self, task_id: str, next_retry_at: str, delay_seconds: float) -> None:
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            for _ in range(50):
                job = self._jobs.get(task_id)
                if not job or job.done():
                    break
                await asyncio.sleep(0.01)
            record = self._store.get_task_job_request(task_id)
            if not record or record.get("next_retry_at") != next_retry_at:
                return
            self.retry(task_id, auto=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[TaskJobRunner] 自动重试 {task_id} 未能入队: {type(exc).__name__}: {exc}")
            self._store.add_task_event(
                task_id,
                "auto_retry_skipped",
                "自动重试未能入队",
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "owner_id": self._owner_id,
                },
            )
        finally:
            if self._scheduled_retries.get(task_id) is asyncio.current_task():
                self._scheduled_retries.pop(task_id, None)

    def _cancel_scheduled_retry(self, task_id: str) -> None:
        scheduled = self._scheduled_retries.pop(task_id, None)
        current_task = None
        with contextlib.suppress(RuntimeError):
            current_task = asyncio.current_task()
        if scheduled and not scheduled.done() and scheduled is not current_task:
            scheduled.cancel()

    def _auto_retry_delay_seconds(self, used_attempts: int) -> float:
        delay = self._auto_retry_initial_delay_seconds * (self._auto_retry_backoff_multiplier ** max(0, used_attempts))
        if self._auto_retry_max_delay_seconds > 0:
            delay = min(delay, self._auto_retry_max_delay_seconds)
        return max(0.0, delay)

    @classmethod
    def _terminal_event_type(cls, status: str, reason: dict[str, Any] | None = None) -> str:
        if status != "failed":
            return status
        retryable = bool((reason or {}).get("retryable"))
        return "failed_retryable" if retryable else "failed_non_retryable"

    @staticmethod
    def _terminal_result_message(status: str, reason: dict[str, Any] | None = None) -> str:
        if status == "completed":
            return "任务已完成"
        if status == "failed":
            retryable = bool((reason or {}).get("retryable"))
            return "任务执行失败（可重试）" if retryable else "任务执行失败（不可重试）"
        if status == "cancelled":
            return (reason or {}).get("message", "任务已取消")
        return status

    @staticmethod
    def _parse_datetime(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _classify_exception(cls, exc: Exception) -> tuple[str, bool]:
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        retryable = isinstance(exc, (ConnectionError, OSError)) or cls._looks_retryable_text(f"{name} {text}")
        return (FAILURE_RETRYABLE_EXCEPTION, True) if retryable else (FAILURE_NON_RETRYABLE_EXCEPTION, False)

    @classmethod
    def _classify_failure_text(cls, text: str) -> tuple[str, bool]:
        retryable = cls._looks_retryable_text(text)
        return (FAILURE_RETRYABLE_EXCEPTION, True) if retryable else (FAILURE_ORCHESTRATOR_FAILED, False)

    @staticmethod
    def _looks_retryable_text(text: str) -> bool:
        lowered = text.lower()
        retryable_markers = (
            "timeout",
            "timed out",
            "connection",
            "connect",
            "network",
            "temporar",
            "rate limit",
            "too many requests",
            "unavailable",
            "busy",
        )
        return any(marker in lowered for marker in retryable_markers)

    def _latest_failure(self, task_id: str) -> dict[str, Any]:
        for event in reversed(self._store.list_task_events(task_id)):
            details = event.get("details") or {}
            failure_type = details.get("failure_type")
            if failure_type:
                return {
                    "event_type": event.get("event_type", ""),
                    "failure_type": failure_type,
                    "retryable": bool(details.get("retryable")),
                    "message": event.get("message", ""),
                    "created_at": event.get("created_at", ""),
                }
            if event.get("event_type") == "timeout":
                return {
                    "event_type": "timeout",
                    "failure_type": FAILURE_TIMEOUT,
                    "retryable": True,
                    "message": event.get("message", ""),
                    "created_at": event.get("created_at", ""),
                }
        return {}

    @staticmethod
    def _is_retryable_for_manual_retry(status: str, latest_failure: dict[str, Any]) -> bool:
        if status == "timeout":
            return True
        failure_type = latest_failure.get("failure_type")
        return bool(latest_failure.get("retryable")) or failure_type in RETRYABLE_FAILURE_TYPES

    async def _execute_orchestrator(self, request: TaskJobRequest) -> IterationResult:
        coro = self._orchestrator.run(
            description=request.description,
            context=request.context,
            active_group_ids=request.active_group_ids,
            task_id=request.task_id,
            on_task_update=self._on_task_update,
        )
        timeout = request.timeout_seconds
        if not timeout:
            return await coro

        execution = asyncio.create_task(coro, name=f"orchestrator:{request.task_id}")
        try:
            done, pending = await asyncio.wait({execution}, timeout=float(timeout))
            if pending:
                if request.task_id:
                    self._orchestrator.cancel_task(request.task_id)
                execution.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await execution
                raise asyncio.TimeoutError
            return next(iter(done)).result()
        except asyncio.CancelledError:
            execution.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await execution
            raise

    def _save_terminal_error(
        self,
        task_id: str,
        description: str,
        status: str,
        result: str,
        created_at: str,
    ) -> None:
        self._save(
            task_id=task_id,
            description=description,
            status=status,
            result=result,
            created_at=created_at,
        )

    def _save(
        self,
        task_id: str,
        description: str,
        status: str,
        result: str,
        created_at: str,
        completed_at: str | None | object = ...,
    ) -> None:
        payload = self._task_payload(
            task_id=task_id,
            description=description,
            status=status,
            result=result,
            created_at=created_at,
        )
        if completed_at is not ...:
            payload["completed_at"] = completed_at
        self._store.save_task(payload)
        self._result_cache[task_id] = payload

    async def _on_task_update(
        self,
        task: Task,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        checkpoint = self._task_checkpoint(task)
        self._store.save_task_checkpoint(task.id, checkpoint)

        message = self._event_message(event_type, details)
        self._store.add_task_event(task.id, event_type, message, details)

    @staticmethod
    def _task_checkpoint(task: Task) -> dict[str, Any]:
        return {
            "task_id": task.id,
            "description": task.description,
            "status": task.status.value,
            "result": task.result,
            "error": task.error,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "sub_tasks": [
                {
                    "id": subtask.id,
                    "description": subtask.description,
                    "dependencies": list(subtask.dependencies),
                    "acceptance_criteria": list(subtask.acceptance_criteria),
                    "status": subtask.status.value,
                    "result": subtask.result,
                    "error": subtask.error,
                    "assigned_agent": subtask.assigned_agent,
                    "attempts": subtask.attempts,
                    "created_at": subtask.created_at,
                    "started_at": subtask.started_at,
                    "completed_at": subtask.completed_at,
                }
                for subtask in task.sub_tasks
            ],
        }

    @staticmethod
    def _event_message(event_type: str, details: dict[str, Any]) -> str:
        subtask_id = details.get("subtask_id")
        messages = {
            "planned": "任务规划已生成",
            "task_running": "任务进入执行中",
            "iteration_started": f"第 {int(details.get('iteration', 0)) + 1} 轮迭代开始",
            "subtask_pending": f"子任务 {subtask_id} 等待执行",
            "subtask_running": f"子任务 {subtask_id} 开始执行",
            "llm_usage": f"子任务 {subtask_id} LLM 调用完成",
            "provider_retry": f"子任务 {subtask_id} provider 调用失败，正在重试",
            "provider_fallback": f"子任务 {subtask_id} 已切换 fallback provider",
            "subtask_retry_scheduled": f"子任务 {subtask_id} 执行失败，已安排重试",
            "subtask_completed": f"子任务 {subtask_id} 已完成",
            "subtask_failed": f"子任务 {subtask_id} 执行失败",
            "task_completed": "任务执行完成",
            "task_failed": "任务执行失败",
            "task_cancelled": "任务已取消",
        }
        return messages.get(event_type, event_type)

    @staticmethod
    def _task_payload(
        task_id: str,
        description: str,
        status: str,
        result: str,
        created_at: str,
    ) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "description": description,
            "status": status,
            "result": result,
            "shared_dir": "",
            "final_score": 0.0,
            "iterations": [],
            "mail_log": "",
            "suggestions": [],
            "created_at": created_at,
        }

    @staticmethod
    def _read_mail_log(shared_dir: str) -> str:
        if not shared_dir:
            return ""
        mail_log_path = Path(shared_dir) / "mail_log.txt"
        if not mail_log_path.exists():
            return ""
        try:
            return mail_log_path.read_text(encoding="utf-8")
        except OSError:
            return ""
