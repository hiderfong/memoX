"""Tasks router"""
import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from coordinator.task_jobs import RETRYABLE_FAILURE_TYPES, TaskJobRequest, TaskJobRunner
from web.state import get_store as _gs

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
_task_job_runner: TaskJobRunner | None = None
_task_job_runner_key: tuple[int, int, int] | None = None


class TaskRequest(BaseModel):
    """任务请求"""
    description: str
    context: dict | None = None
    generate_suggestions: bool = True
    active_group_ids: list[str] | None = None
    timeout_seconds: int | None = None  # 任务超时（秒）


class FeedbackRequest(BaseModel):
    feedback: str


class RetryTaskRequest(BaseModel):
    force: bool = False


def _get_task_job_runner() -> TaskJobRunner:
    import web.api as _api_module

    global _task_job_runner, _task_job_runner_key
    orchestrator = getattr(_api_module, "_orchestrator", None)
    task_planner = getattr(_api_module, "_task_planner", None)
    result_cache = getattr(_api_module, "_task_results", {})
    config = getattr(_api_module, "_config", None)
    store = _gs()
    if not orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")
    if not store:
        raise HTTPException(status_code=500, detail="Persistence store not initialized")

    key = (id(orchestrator), id(task_planner), id(store))
    if _task_job_runner is None or _task_job_runner_key != key:
        init_task_job_runner(orchestrator, task_planner, store, result_cache, config=config)
    return _task_job_runner


def init_task_job_runner(orchestrator, task_planner, store, result_cache, config=None) -> TaskJobRunner:
    """Initialize the shared task job runner used by API routes and startup recovery."""
    global _task_job_runner, _task_job_runner_key
    coordinator = getattr(config, "coordinator", None)
    _task_job_runner = TaskJobRunner(
        orchestrator,
        task_planner,
        store,
        result_cache,
        auto_retry_enabled=bool(getattr(coordinator, "task_auto_retry_enabled", False)),
        auto_retry_max_attempts=int(getattr(coordinator, "task_auto_retry_max_attempts", 0) or 0),
        auto_retry_initial_delay_seconds=float(
            getattr(coordinator, "task_auto_retry_initial_delay_seconds", 30.0) or 0.0
        ),
        auto_retry_max_delay_seconds=float(getattr(coordinator, "task_auto_retry_max_delay_seconds", 300.0) or 0.0),
        auto_retry_backoff_multiplier=float(getattr(coordinator, "task_auto_retry_backoff_multiplier", 2.0) or 1.0),
    )
    _task_job_runner_key = (id(orchestrator), id(task_planner), id(store))
    return _task_job_runner


def _attach_persistent_task_metadata(task: dict, store) -> dict:
    task_id = task.get("task_id") or task.get("id")
    if not task_id or not store:
        return task

    checkpoint = store.get_task_checkpoint(task_id)
    if checkpoint:
        task["checkpoint"] = checkpoint
        task["sub_tasks"] = checkpoint.get("sub_tasks", [])

    job = store.get_task_job_request(task_id)
    if job:
        task["job"] = {
            "lease_owner": job.get("lease_owner", ""),
            "lease_expires_at": job.get("lease_expires_at", ""),
            "recovery_count": job.get("recovery_count", 0),
            "last_recovered_at": job.get("last_recovered_at", ""),
            "auto_retry_count": job.get("auto_retry_count", 0),
            "next_retry_at": job.get("next_retry_at", ""),
            "updated_at": job.get("updated_at", ""),
        }
    latest_failure = _latest_failure_from_events(store.list_task_events(task_id))
    if latest_failure:
        task["last_failure"] = latest_failure
    return task


def _latest_failure_from_events(events: list[dict]) -> dict:
    for event in reversed(events):
        details = event.get("details") or {}
        failure_type = details.get("failure_type")
        if not failure_type and event.get("event_type") == "timeout":
            failure_type = "timeout"
        if failure_type:
            retryable = bool(
                details.get("retryable")
                or failure_type in {"timeout", "lease_lost", "retryable_exception"}
            )
            return {
                "event_type": event.get("event_type", ""),
                "failure_type": failure_type,
                "retryable": retryable,
                "message": event.get("message", ""),
                "created_at": event.get("created_at", ""),
            }
    return {}


_TRACE_EVENT_LABELS = {
    "queued": "任务已入队",
    "recovery_queued": "恢复执行已入队",
    "retry_queued": "手动重试已入队",
    "auto_retry_queued": "自动重试已入队",
    "auto_retry_scheduled": "自动重试已排期",
    "auto_retry_exhausted": "自动重试次数已用尽",
    "planned": "规划完成",
    "task_running": "任务开始执行",
    "running": "任务执行中",
    "iteration_started": "迭代开始",
    "subtask_pending": "子任务等待执行",
    "subtask_running": "子任务开始执行",
    "llm_usage": "LLM 调用完成",
    "provider_retry": "Provider 调用重试",
    "provider_fallback": "Provider fallback 切换",
    "subtask_retry_scheduled": "子任务重试已排期",
    "subtask_completed": "子任务完成",
    "subtask_failed": "子任务失败",
    "task_completed": "任务完成",
    "completed": "任务完成",
    "failed": "任务失败",
    "failed_retryable": "任务失败，可重试",
    "failed_non_retryable": "任务失败，需人工处理",
    "timeout": "任务超时",
    "cancel_requested": "取消请求已发送",
    "cancelled": "任务已取消",
    "lease_lost": "执行租约丢失",
    "lease_lost_stopped": "本地执行已停止",
}


def _trace_event_stage(event_type: str) -> str:
    if event_type.startswith("subtask_"):
        return "subtask"
    if event_type.startswith("provider_"):
        return "provider"
    if event_type == "llm_usage":
        return "llm"
    if "retry" in event_type or event_type in {"recovery_queued", "lease_lost", "lease_lost_stopped"}:
        return "recovery"
    if event_type.startswith("iteration_"):
        return "iteration"
    if event_type == "planned":
        return "planning"
    return "task"


def _trace_event_severity(event_type: str) -> str:
    if event_type in {"completed", "task_completed", "subtask_completed"}:
        return "success"
    if event_type in {"failed", "failed_non_retryable", "subtask_failed", "auto_retry_exhausted"}:
        return "error"
    if event_type in {
        "failed_retryable",
        "timeout",
        "lease_lost",
        "lease_lost_stopped",
        "provider_retry",
        "provider_fallback",
        "subtask_retry_scheduled",
        "auto_retry_scheduled",
        "cancel_requested",
        "cancelled",
    }:
        return "warning"
    if event_type in {"running", "task_running", "subtask_running", "iteration_started", "llm_usage"}:
        return "processing"
    return "default"


def _extract_subtask_id(event: dict[str, Any]) -> str:
    details = event.get("details") or {}
    if not isinstance(details, dict):
        return ""
    subtask_id = details.get("subtask_id") or details.get("sub_task_id")
    return str(subtask_id) if subtask_id else ""


def _preview_value(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _normalize_trace_event(event: dict[str, Any]) -> dict[str, Any]:
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    event_type = str(event.get("event_type") or "")
    actor = {
        key: details[key]
        for key in ("worker_id", "worker", "provider", "model")
        if details.get(key)
    }
    return {
        "id": event.get("id"),
        "task_id": event.get("task_id", ""),
        "event_type": event_type,
        "label": _TRACE_EVENT_LABELS.get(event_type, event_type or "事件"),
        "message": event.get("message", ""),
        "stage": _trace_event_stage(event_type),
        "severity": _trace_event_severity(event_type),
        "subtask_id": _extract_subtask_id(event),
        "actor": actor,
        "details": details,
        "created_at": event.get("created_at", ""),
        "source": "task_event",
    }


def _subtask_ids_from_checkpoint_and_events(checkpoint: dict[str, Any] | None, events: list[dict]) -> set[str]:
    subtask_ids: set[str] = set()
    checkpoint = checkpoint or {}
    for raw in checkpoint.get("sub_tasks", []) if isinstance(checkpoint.get("sub_tasks"), list) else []:
        if isinstance(raw, dict) and raw.get("id"):
            subtask_ids.add(str(raw["id"]))
    for event in events:
        subtask_id = _extract_subtask_id(event)
        if subtask_id:
            subtask_ids.add(subtask_id)
    return subtask_ids


def _normalize_tool_audit_event(event: dict[str, Any]) -> dict[str, Any]:
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    status = str(details.get("status") or "unknown")
    severity = "success" if status == "success" else "warning" if status == "rejected" else "error"
    tool_name = str(event.get("resource_id") or details.get("tool_name") or "tool")
    actor = {
        key: details[key]
        for key in ("worker_id", "worker_name")
        if details.get(key)
    }
    return {
        "id": f"audit:{event.get('id')}",
        "task_id": details.get("task_id", ""),
        "event_type": "tool_call",
        "label": f"工具调用 {tool_name}",
        "message": str(details.get("error") or details.get("message") or ""),
        "stage": "tool",
        "severity": severity,
        "subtask_id": str(details.get("subtask_id") or ""),
        "actor": actor,
        "details": {
            "tool": tool_name,
            "status": status,
            "arguments": details.get("arguments", {}),
            "result": details.get("result", {}),
            "error": details.get("error", ""),
        },
        "created_at": event.get("timestamp", ""),
        "source": "tool_audit",
    }


def _normalize_worker_log_event(event: dict[str, Any]) -> dict[str, Any]:
    meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
    level = str(event.get("level") or "info").lower()
    severity = "error" if level == "error" else "warning" if level in {"warning", "warn"} else "default"
    message = str(event.get("message") or "")
    is_llm_call = "input_tokens" in meta or "output_tokens" in meta
    return {
        "id": event.get("id") or f"worker_log:{event.get('worker_id', '')}:{event.get('timestamp', '')}:{message}",
        "task_id": meta.get("task_id", ""),
        "event_type": "llm_call" if is_llm_call else "worker_log",
        "label": "LLM 调用完成" if is_llm_call else "Worker 日志",
        "message": message,
        "stage": "llm" if is_llm_call else "worker_log",
        "severity": "processing" if is_llm_call else severity,
        "subtask_id": str(meta.get("subtask_id") or ""),
        "actor": {"worker_id": str(event.get("worker_id") or "")},
        "details": meta,
        "created_at": event.get("created_at") or event.get("timestamp", ""),
        "source": "worker_log",
    }


def _collect_task_tool_audit_events(store, task_id: str, subtask_ids: set[str]) -> list[dict[str, Any]]:
    events_by_id: dict[str, dict[str, Any]] = {}
    query_task_ids = [task_id, *sorted(subtask_ids)]
    for query_task_id in query_task_ids:
        for event in store.list_audit_events(
            resource="tool",
            action="tool_call",
            task_id=query_task_id,
            limit=100,
        ):
            event_id = str(event.get("id") or f"{event.get('timestamp')}:{event.get('resource_id')}")
            events_by_id[event_id] = event
    return list(events_by_id.values())


def _collect_task_worker_logs(
    store,
    task_id: str,
    subtask_ids: set[str],
    include_llm_logs: bool = True,
) -> list[dict[str, Any]]:
    logs_by_id: dict[str, dict[str, Any]] = {}
    for log in store.list_worker_logs(task_id=task_id, limit=300):
        logs_by_id[str(log.get("id"))] = log
    for subtask_id in subtask_ids:
        for log in store.list_worker_logs(subtask_id=subtask_id, limit=100):
            logs_by_id[str(log.get("id"))] = log
    logs = list(logs_by_id.values())
    if include_llm_logs:
        return logs
    return [
        log
        for log in logs
        if not (
            isinstance(log.get("meta"), dict)
            and ("input_tokens" in log["meta"] or "output_tokens" in log["meta"])
        )
    ]


def _trace_event_matches(event: dict[str, Any], filters: dict[str, str]) -> bool:
    if filters.get("subtask_id") and event.get("subtask_id") != filters["subtask_id"]:
        return False
    if filters.get("stage") and event.get("stage") != filters["stage"]:
        return False
    if filters.get("severity") and event.get("severity") != filters["severity"]:
        return False
    if filters.get("event_type") and event.get("event_type") != filters["event_type"]:
        return False
    if filters.get("worker_id"):
        actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if filters["worker_id"] not in {str(actor.get("worker_id") or ""), str(details.get("worker_id") or "")}:
            return False
    if filters.get("tool_name"):
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        if str(details.get("tool") or details.get("tool_name") or "") != filters["tool_name"]:
            return False
    if filters.get("failure_type"):
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        failure_type = str(details.get("failure_type") or event.get("event_type") or "")
        if failure_type != filters["failure_type"]:
            return False
    return True


def _event_sort_key(event: dict[str, Any]) -> tuple[str, str]:
    return (str(event.get("created_at") or ""), str(event.get("id") or ""))


def _build_task_trace(
    task: dict[str, Any],
    checkpoint: dict[str, Any] | None,
    events: list[dict],
    tool_audit_events: list[dict] | None = None,
    worker_logs: list[dict] | None = None,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    task_id = task.get("task_id") or task.get("id") or (checkpoint or {}).get("task_id") or ""
    checkpoint = checkpoint or {}
    checkpoint_subtasks = checkpoint.get("sub_tasks") if isinstance(checkpoint.get("sub_tasks"), list) else []
    subtask_records: dict[str, dict[str, Any]] = {}

    for raw in checkpoint_subtasks:
        if not isinstance(raw, dict):
            continue
        subtask_id = str(raw.get("id") or "")
        if not subtask_id:
            continue
        subtask_records[subtask_id] = {
            "id": subtask_id,
            "description": raw.get("description", ""),
            "status": raw.get("status", "unknown"),
            "assigned_agent": raw.get("assigned_agent", ""),
            "attempts": raw.get("attempts", 0),
            "dependencies": raw.get("dependencies", []),
            "acceptance_criteria": raw.get("acceptance_criteria", []),
            "created_at": raw.get("created_at", ""),
            "started_at": raw.get("started_at", ""),
            "completed_at": raw.get("completed_at", ""),
            "result_preview": _preview_value(raw.get("result")),
            "error": raw.get("error", ""),
            "events": [],
        }

    normalized_events = [_normalize_trace_event(event) for event in events]
    normalized_events.extend(_normalize_tool_audit_event(event) for event in tool_audit_events or [])
    normalized_events.extend(_normalize_worker_log_event(event) for event in worker_logs or [])
    normalized_events.sort(key=_event_sort_key)
    filters = {key: value for key, value in (filters or {}).items() if value}
    filtered_events = [
        event for event in normalized_events if _trace_event_matches(event, filters)
    ]
    unassigned_events: list[dict[str, Any]] = []
    for event in filtered_events:
        subtask_id = event.get("subtask_id") or ""
        if subtask_id:
            subtask_records.setdefault(
                subtask_id,
                {
                    "id": subtask_id,
                    "description": "",
                    "status": "unknown",
                    "assigned_agent": "",
                    "attempts": 0,
                    "dependencies": [],
                    "acceptance_criteria": [],
                    "created_at": "",
                    "started_at": "",
                    "completed_at": "",
                    "result_preview": "",
                    "error": "",
                    "events": [],
                },
            )["events"].append(event)
        else:
            unassigned_events.append(event)

    if filters:
        filtered_subtasks = []
        for subtask in subtask_records.values():
            keep = bool(subtask["events"])
            if filters.get("subtask_id") and subtask["id"] == filters["subtask_id"]:
                keep = True
            if keep:
                filtered_subtasks.append(subtask)
        subtasks = filtered_subtasks
    else:
        subtasks = list(subtask_records.values())
    status_counts: dict[str, int] = {}
    for subtask in subtasks:
        status = str(subtask.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    retry_event_types = {"provider_retry", "subtask_retry_scheduled", "retry_queued", "auto_retry_queued"}
    llm_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 0}
    for event in filtered_events:
        if event["event_type"] not in {"llm_call", "llm_usage"}:
            continue
        details = event.get("details") or {}
        input_tokens = int(details.get("input_tokens") or 0)
        output_tokens = int(details.get("output_tokens") or 0)
        llm_usage["input_tokens"] += input_tokens
        llm_usage["output_tokens"] += output_tokens
        llm_usage["total_tokens"] += input_tokens + output_tokens
        llm_usage["call_count"] += 1

    tool_events = [event for event in filtered_events if event["event_type"] == "tool_call"]
    summary = {
        "event_count": len(filtered_events),
        "total_event_count": len(normalized_events),
        "task_event_count": sum(1 for event in filtered_events if event.get("source") == "task_event"),
        "tool_call_count": len(tool_events),
        "tool_rejected_count": sum(1 for event in tool_events if (event.get("details") or {}).get("status") == "rejected"),
        "worker_log_count": sum(1 for event in filtered_events if event.get("source") == "worker_log"),
        "llm_usage": llm_usage,
        "subtask_count": len(subtasks),
        "status_counts": status_counts,
        "retry_count": sum(1 for event in filtered_events if event["event_type"] in retry_event_types),
        "fallback_count": sum(1 for event in filtered_events if event["event_type"] == "provider_fallback"),
        "failure_count": sum(1 for event in filtered_events if event["severity"] == "error"),
        "first_event_at": filtered_events[0]["created_at"] if filtered_events else "",
        "last_event_at": filtered_events[-1]["created_at"] if filtered_events else "",
    }

    return {
        "task_id": task_id,
        "description": task.get("description") or checkpoint.get("description", ""),
        "status": task.get("status") or checkpoint.get("status", "unknown"),
        "created_at": task.get("created_at") or checkpoint.get("created_at", ""),
        "started_at": task.get("started_at") or checkpoint.get("started_at", ""),
        "completed_at": task.get("completed_at") or checkpoint.get("completed_at", ""),
        "checkpoint_updated_at": checkpoint.get("updated_at", ""),
        "summary": summary,
        "subtasks": subtasks,
        "unassigned_events": unassigned_events,
        "timeline": filtered_events,
        "filters": filters,
    }


def _load_task_trace_payload(task_id: str, filters: dict[str, str] | None = None) -> dict:
    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="Persistence store not initialized")
    task = store.get_task(task_id)
    checkpoint = store.get_task_checkpoint(task_id)
    if not task and not checkpoint:
        raise HTTPException(status_code=404, detail="Task not found")
    events = store.list_task_events(task_id)
    subtask_ids = _subtask_ids_from_checkpoint_and_events(checkpoint, events)
    tool_audit_events = _collect_task_tool_audit_events(store, task_id, subtask_ids)
    worker_logs = _collect_task_worker_logs(
        store,
        task_id,
        subtask_ids,
        include_llm_logs=not any(event.get("event_type") == "llm_usage" for event in events),
    )
    return _build_task_trace(
        task or {"task_id": task_id},
        checkpoint,
        events,
        tool_audit_events,
        worker_logs,
        filters,
    )


def _event_brief(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event.get("id"),
        "created_at": event.get("created_at", ""),
        "stage": event.get("stage", ""),
        "severity": event.get("severity", ""),
        "event_type": event.get("event_type", ""),
        "label": event.get("label", ""),
        "subtask_id": event.get("subtask_id", ""),
        "actor": event.get("actor", {}),
        "message": _preview_value(event.get("message"), limit=240),
        "details": event.get("details", {}),
    }


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _build_task_diagnosis(trace: dict[str, Any]) -> dict[str, Any]:
    events = trace.get("timeline") if isinstance(trace.get("timeline"), list) else []
    subtasks = trace.get("subtasks") if isinstance(trace.get("subtasks"), list) else []
    summary = trace.get("summary") if isinstance(trace.get("summary"), dict) else {}

    failure_events = [
        event
        for event in events
        if event.get("severity") == "error"
        or event.get("event_type") in {"failed", "failed_retryable", "failed_non_retryable", "timeout"}
    ]
    warning_events = [event for event in events if event.get("severity") == "warning"]
    tool_rejections = [
        event
        for event in events
        if event.get("event_type") == "tool_call"
        and (event.get("details") or {}).get("status") == "rejected"
    ]
    provider_events = [
        event
        for event in events
        if event.get("event_type") in {"provider_retry", "provider_fallback"}
    ]
    worker_errors = [
        event
        for event in events
        if event.get("stage") == "worker_log" and event.get("severity") == "error"
    ]
    failed_subtasks = [
        subtask
        for subtask in subtasks
        if subtask.get("status") in {"failed", "timeout", "cancelled"} or subtask.get("error")
    ]
    llm_events = [
        event
        for event in events
        if event.get("event_type") in {"llm_usage", "llm_call"}
    ]
    max_llm_call = max(
        (
            {
                "subtask_id": event.get("subtask_id", ""),
                "worker_id": ((event.get("actor") or {}).get("worker_id") or (event.get("details") or {}).get("worker_id") or ""),
                "input_tokens": int((event.get("details") or {}).get("input_tokens") or 0),
                "output_tokens": int((event.get("details") or {}).get("output_tokens") or 0),
                "total_tokens": int((event.get("details") or {}).get("total_tokens") or 0)
                or int((event.get("details") or {}).get("input_tokens") or 0)
                + int((event.get("details") or {}).get("output_tokens") or 0),
                "created_at": event.get("created_at", ""),
            }
            for event in llm_events
        ),
        key=lambda item: item["total_tokens"],
        default={"subtask_id": "", "worker_id": "", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "created_at": ""},
    )

    root_causes: list[str] = []
    recommendations: list[str] = []
    if failure_events or failed_subtasks:
        _append_unique(root_causes, "存在失败事件或失败子任务，任务结果需要人工复核。")
        _append_unique(recommendations, "优先查看 error 级别事件和失败子任务的 worker 日志，再决定重试或拆分任务。")
    if any(event.get("event_type") == "timeout" for event in events):
        _append_unique(root_causes, "任务发生超时，可能是子任务耗时过长、工具调用阻塞或模型响应较慢。")
        _append_unique(recommendations, "缩小任务范围或提高 timeout_seconds，并从 checkpoint 续跑未完成子任务。")
    if tool_rejections:
        tools = sorted({str((event.get("details") or {}).get("tool") or "") for event in tool_rejections if (event.get("details") or {}).get("tool")})
        _append_unique(root_causes, f"有 {len(tool_rejections)} 次工具调用被策略拦截" + (f": {', '.join(tools)}。" if tools else "。"))
        _append_unique(recommendations, "检查对应工具的参数和权限策略；若业务允许，再调整工具策略而不是放宽全局权限。")
    if provider_events:
        fallback_count = sum(1 for event in provider_events if event.get("event_type") == "provider_fallback")
        retry_count = sum(1 for event in provider_events if event.get("event_type") == "provider_retry")
        _append_unique(root_causes, f"Provider 调用出现波动，重试 {retry_count} 次，fallback {fallback_count} 次。")
        _append_unique(recommendations, "保留 fallback 路由，同时检查主 provider 的超时、限流、模型名和网络稳定性。")
    if worker_errors:
        _append_unique(root_causes, f"Worker 日志中有 {len(worker_errors)} 条错误记录。")
        _append_unique(recommendations, "按 worker_id 过滤执行树，定位错误集中在哪个 Worker 或子任务。")
    if max_llm_call["total_tokens"] >= 12000:
        _append_unique(root_causes, f"单次 LLM 调用 token 峰值较高: {max_llm_call['total_tokens']} tokens。")
        _append_unique(recommendations, "压缩上下文、拆分输入材料，或为长上下文任务配置更合适的模型。")
    if not root_causes:
        _append_unique(root_causes, "未发现明确失败信号，任务 trace 看起来处于正常范围。")
        _append_unique(recommendations, "如结果质量仍不理想，可查看子任务验收标准和最终产物内容。")

    level = "ok"
    if failure_events or failed_subtasks or worker_errors:
        level = "critical"
    elif warning_events or tool_rejections or provider_events or max_llm_call["total_tokens"] >= 12000:
        level = "warning"

    evidence_source = [*failure_events, *tool_rejections, *provider_events, *worker_errors]
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in evidence_source:
        key = str(event.get("id") or f"{event.get('event_type')}:{event.get('created_at')}:{event.get('message')}")
        if key in seen:
            continue
        seen.add(key)
        evidence.append(_event_brief(event))
        if len(evidence) >= 8:
            break

    return {
        "task_id": trace.get("task_id", ""),
        "status": trace.get("status", "unknown"),
        "level": level,
        "generated_at": datetime.now().isoformat(),
        "headline": "任务需要处理" if level == "critical" else "任务存在风险信号" if level == "warning" else "未发现明显异常",
        "root_causes": root_causes,
        "recommendations": recommendations,
        "evidence": evidence,
        "metrics": {
            "event_count": summary.get("event_count", 0),
            "failure_count": summary.get("failure_count", 0),
            "retry_count": summary.get("retry_count", 0),
            "fallback_count": summary.get("fallback_count", 0),
            "tool_call_count": summary.get("tool_call_count", 0),
            "tool_rejected_count": summary.get("tool_rejected_count", 0),
            "worker_log_count": summary.get("worker_log_count", 0),
            "llm_usage": summary.get("llm_usage", {}),
            "max_llm_call": max_llm_call,
            "failed_subtasks": [
                {
                    "id": subtask.get("id", ""),
                    "status": subtask.get("status", ""),
                    "assigned_agent": subtask.get("assigned_agent", ""),
                    "error": _preview_value(subtask.get("error"), limit=240),
                }
                for subtask in failed_subtasks
            ],
        },
    }


def _build_task_retry_suggestion(
    task_id: str,
    task: dict[str, Any],
    job: dict[str, Any] | None,
    events: list[dict],
    trace: dict[str, Any],
    diagnosis: dict[str, Any],
) -> dict[str, Any]:
    status = str(task.get("status") or trace.get("status") or "unknown")
    latest_failure = _latest_failure_from_events(events)
    failure_type = str(latest_failure.get("failure_type") or "")
    retryable_without_force = status == "timeout" or bool(latest_failure.get("retryable")) or failure_type in RETRYABLE_FAILURE_TYPES
    active = status in {"pending", "queued", "running"} or bool((job or {}).get("lease_owner"))
    waiting_auto_retry = bool((job or {}).get("next_retry_at"))
    terminal = status in {"completed", "failed", "cancelled", "timeout"}
    diagnosis_metrics = diagnosis.get("metrics") if isinstance(diagnosis.get("metrics"), dict) else {}

    blockers: list[dict[str, str]] = []
    steps: list[str] = []

    if active:
        blockers.append({"type": "task_active", "message": "任务仍在运行或持有执行租约，当前不应重复入队。"})
    if waiting_auto_retry:
        blockers.append({
            "type": "auto_retry_scheduled",
            "message": f"任务已安排自动重试：{job.get('next_retry_at')}",
        })
    if status == "completed":
        blockers.append({"type": "already_completed", "message": "任务已完成，通常不需要重试。"})
    if diagnosis_metrics.get("tool_rejected_count", 0) > 0:
        blockers.append({
            "type": "tool_policy_rejection",
            "message": "存在工具策略拦截；直接重试大概率会再次失败，需先修正工具参数或策略。",
        })
        _append_unique(steps, "先按诊断证据检查被拦截的工具调用，修正参数、输入路径或工具策略。")
    if diagnosis_metrics.get("fallback_count", 0) > 0 or diagnosis_metrics.get("retry_count", 0) > 0:
        _append_unique(steps, "确认主 provider 的模型名、限流、超时和网络状态；保留 fallback 路由后再重试。")
    if status == "timeout" or failure_type == "timeout":
        _append_unique(steps, "优先从 checkpoint 续跑；必要时提高 timeout_seconds 或拆小子任务。")
    if diagnosis_metrics.get("failed_subtasks"):
        _append_unique(steps, "先按失败子任务过滤执行树，确认失败集中在哪个 worker 或工具。")

    hard_blocked = active or waiting_auto_retry or status == "completed"
    force_required = terminal and status == "failed" and not retryable_without_force and not hard_blocked
    retry_enabled = terminal and not hard_blocked and (retryable_without_force or force_required)

    if retry_enabled and force_required:
        mode = "manual_force_after_fix"
        headline = "可在修复阻塞点后强制重试"
        _append_unique(steps, "确认上述阻塞点已处理后，使用 force=true 重新入队。")
    elif retry_enabled:
        mode = "manual_retry"
        headline = "适合手动重试"
        _append_unique(steps, "使用现有 checkpoint 重新入队，系统会跳过已完成子任务。")
    elif waiting_auto_retry:
        mode = "wait_auto_retry"
        headline = "建议等待自动重试"
    elif active:
        mode = "already_running"
        headline = "任务仍在执行中"
    elif status == "completed":
        mode = "not_needed"
        headline = "任务已完成，无需重试"
    else:
        mode = "blocked"
        headline = "暂不建议直接重试"

    if not steps:
        _append_unique(steps, "查看诊断证据和执行树筛选项，确认失败原因后再决定是否重试。")

    confidence = "high"
    if force_required or diagnosis_metrics.get("tool_rejected_count", 0) > 0:
        confidence = "medium"
    if not events:
        confidence = "low"

    return {
        "task_id": task_id,
        "status": status,
        "mode": mode,
        "headline": headline,
        "retryable": retry_enabled and not force_required,
        "force_required": force_required,
        "confidence": confidence,
        "latest_failure": latest_failure,
        "blockers": blockers,
        "steps": steps,
        "retry_request": {
            "enabled": retry_enabled,
            "method": "POST",
            "path": f"/api/tasks/{task_id}/retry",
            "body": {"force": force_required},
        },
        "diagnosis_level": diagnosis.get("level", "ok"),
        "metrics": {
            "failure_count": diagnosis_metrics.get("failure_count", 0),
            "retry_count": diagnosis_metrics.get("retry_count", 0),
            "fallback_count": diagnosis_metrics.get("fallback_count", 0),
            "tool_rejected_count": diagnosis_metrics.get("tool_rejected_count", 0),
            "llm_usage": diagnosis_metrics.get("llm_usage", {}),
        },
    }


def _markdown_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- 无"


def _format_metric_value(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(f"{key}={val}" for key, val in value.items()) or "{}"
    if isinstance(value, list):
        return str(len(value))
    return str(value)


def _build_task_diagnosis_report(
    trace: dict[str, Any],
    diagnosis: dict[str, Any],
    retry_suggestion: dict[str, Any],
) -> dict[str, Any]:
    task_id = str(trace.get("task_id") or diagnosis.get("task_id") or "")
    safe_task_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in task_id) or "task"
    generated_at = datetime.now().isoformat()
    metrics = diagnosis.get("metrics") if isinstance(diagnosis.get("metrics"), dict) else {}
    evidence = diagnosis.get("evidence") if isinstance(diagnosis.get("evidence"), list) else []
    failed_subtasks = metrics.get("failed_subtasks") if isinstance(metrics.get("failed_subtasks"), list) else []
    llm_usage = metrics.get("llm_usage") if isinstance(metrics.get("llm_usage"), dict) else {}
    max_llm_call = metrics.get("max_llm_call") if isinstance(metrics.get("max_llm_call"), dict) else {}
    blockers = retry_suggestion.get("blockers") if isinstance(retry_suggestion.get("blockers"), list) else []
    steps = retry_suggestion.get("steps") if isinstance(retry_suggestion.get("steps"), list) else []

    lines = [
        f"# MemoX Task Diagnosis Report: {task_id}",
        "",
        f"- Generated At: {generated_at}",
        f"- Task Status: {trace.get('status', 'unknown')}",
        f"- Diagnosis Level: {diagnosis.get('level', 'unknown')}",
        f"- Headline: {diagnosis.get('headline', '')}",
        "",
        "## Summary Metrics",
        "",
        f"- Events: {metrics.get('event_count', 0)}",
        f"- Failures: {metrics.get('failure_count', 0)}",
        f"- Retries: {metrics.get('retry_count', 0)}",
        f"- Fallbacks: {metrics.get('fallback_count', 0)}",
        f"- Tool Calls: {metrics.get('tool_call_count', 0)}",
        f"- Tool Rejections: {metrics.get('tool_rejected_count', 0)}",
        f"- Worker Logs: {metrics.get('worker_log_count', 0)}",
        f"- LLM Usage: {_format_metric_value(llm_usage)}",
        f"- Max LLM Call: {_format_metric_value(max_llm_call)}",
        "",
        "## Possible Causes",
        "",
        _markdown_bullets([str(item) for item in diagnosis.get("root_causes", [])]),
        "",
        "## Recommended Actions",
        "",
        _markdown_bullets([str(item) for item in diagnosis.get("recommendations", [])]),
        "",
        "## Retry Suggestion",
        "",
        f"- Mode: {retry_suggestion.get('mode', '')}",
        f"- Headline: {retry_suggestion.get('headline', '')}",
        f"- Retry Enabled: {bool((retry_suggestion.get('retry_request') or {}).get('enabled'))}",
        f"- Force Required: {bool(retry_suggestion.get('force_required'))}",
        f"- Confidence: {retry_suggestion.get('confidence', '')}",
        "",
        "### Retry Blockers",
        "",
        _markdown_bullets([str(item.get("message", "")) for item in blockers if isinstance(item, dict)]),
        "",
        "### Retry Steps",
        "",
        _markdown_bullets([str(item) for item in steps]),
        "",
        "## Failed Subtasks",
        "",
    ]
    if failed_subtasks:
        for subtask in failed_subtasks:
            if not isinstance(subtask, dict):
                continue
            lines.append(
                f"- {subtask.get('id', '')}: status={subtask.get('status', '')}, "
                f"worker={subtask.get('assigned_agent', '')}, error={subtask.get('error', '')}"
            )
    else:
        lines.append("- 无")

    lines.extend(["", "## Key Evidence", ""])
    if evidence:
        for event in evidence[:8]:
            if not isinstance(event, dict):
                continue
            lines.append(
                f"- [{event.get('created_at', '')}] {event.get('stage', '')}/"
                f"{event.get('severity', '')} {event.get('label', event.get('event_type', ''))}"
                f" subtask={event.get('subtask_id', '')} message={event.get('message', '')}"
            )
    else:
        lines.append("- 无")

    markdown = "\n".join(lines).strip() + "\n"
    return {
        "task_id": task_id,
        "filename": f"memox-diagnosis-{safe_task_id}.md",
        "content_type": "text/markdown; charset=utf-8",
        "generated_at": generated_at,
        "markdown": markdown,
        "share_text": markdown[:4000],
    }


@router.post("")
async def create_task(request: TaskRequest) -> dict:
    """创建后台任务，并立即返回可轮询的任务记录。"""
    runner = _get_task_job_runner()
    return runner.submit(
        TaskJobRequest(
            description=request.description,
            context=request.context or {},
            generate_suggestions=request.generate_suggestions,
            active_group_ids=request.active_group_ids,
            timeout_seconds=request.timeout_seconds,
        )
    )


@router.get("")
async def list_tasks() -> list[dict]:
    """列出所有任务（合并内存 + SQLite 持久化）"""
    import web.api as _api_module
    _task_planner = getattr(_api_module, "_task_planner", None)

    # 优先从 SQLite 加载持久化历史
    store = _gs()
    if store:
        persisted = store.list_tasks(limit=100)
        if persisted:
            return [_attach_persistent_task_metadata(dict(task), store) for task in persisted]

    # 回退到 TaskPlanner 的内存存储
    if _task_planner:
        return _task_planner.list_tasks()
    return []


@router.get("/running")
async def list_running_tasks() -> list[str]:
    """列出正在运行的任务 ID"""
    import web.api as _api_module
    _orchestrator = getattr(_api_module, "_orchestrator", None)

    running: set[str] = set()
    if _orchestrator:
        running.update(_orchestrator.list_running_tasks())
    with contextlib.suppress(HTTPException):
        running.update(_get_task_job_runner().list_running())
    return sorted(running)


@router.get("/{task_id}/files")
async def get_task_files(task_id: str) -> dict:
    """获取任务 shared/ 目录的文件树和内容"""
    import web.api as _api_module
    _task_results = getattr(_api_module, "_task_results", {})

    cached = _task_results.get(task_id)
    if not cached:
        store = _gs()
        if store:
            cached = store.get_task(task_id)
    if not cached or not cached.get("shared_dir"):
        raise HTTPException(status_code=404, detail="Task not found or no shared directory")

    shared_dir = Path(cached["shared_dir"])
    if not shared_dir.exists():
        return {"task_id": task_id, "files": []}

    files = []
    for file_path in sorted(shared_dir.rglob("*")):
        if not file_path.is_file():
            continue
        rel_path = str(file_path.relative_to(shared_dir))
        # 跳过 mail_log.txt（已在 mail_log 字段中返回）
        if file_path.name == "mail_log.txt":
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            content = "(二进制文件，无法预览)"
        files.append(
            {
                "path": rel_path,
                "name": file_path.name,
                "size": file_path.stat().st_size,
                "content": content[:5000],  # 截断过长内容
                "truncated": len(content) > 5000 if isinstance(content, str) else False,
            }
        )

    return {"task_id": task_id, "files": files}


@router.get("/{task_id}/events")
async def get_task_events(task_id: str) -> dict:
    """获取任务事件日志。"""
    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="Persistence store not initialized")
    if not store.get_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "events": store.list_task_events(task_id)}


@router.get("/{task_id}/trace")
async def get_task_trace(
    task_id: str,
    subtask_id: str | None = Query(default=None),
    worker_id: str | None = Query(default=None),
    tool_name: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    failure_type: str | None = Query(default=None),
) -> dict:
    """获取任务执行树和归一化 trace。"""
    filters = {
        "subtask_id": subtask_id or "",
        "worker_id": worker_id or "",
        "tool_name": tool_name or "",
        "stage": stage or "",
        "severity": severity or "",
        "event_type": event_type or "",
        "failure_type": failure_type or "",
    }
    return _load_task_trace_payload(task_id, filters)


@router.get("/{task_id}/diagnosis")
async def get_task_diagnosis(task_id: str) -> dict:
    """获取任务排障诊断摘要。"""
    return _build_task_diagnosis(_load_task_trace_payload(task_id))


@router.get("/{task_id}/retry-suggestion")
async def get_task_retry_suggestion(task_id: str) -> dict:
    """获取失败任务的重试建议。"""
    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="Persistence store not initialized")
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    events = store.list_task_events(task_id)
    trace = _load_task_trace_payload(task_id)
    diagnosis = _build_task_diagnosis(trace)
    return _build_task_retry_suggestion(
        task_id,
        task,
        store.get_task_job_request(task_id),
        events,
        trace,
        diagnosis,
    )


@router.get("/{task_id}/diagnosis-report")
async def get_task_diagnosis_report(task_id: str) -> dict:
    """导出任务诊断 Markdown 报告。"""
    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="Persistence store not initialized")
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    events = store.list_task_events(task_id)
    trace = _load_task_trace_payload(task_id)
    diagnosis = _build_task_diagnosis(trace)
    retry_suggestion = _build_task_retry_suggestion(
        task_id,
        task,
        store.get_task_job_request(task_id),
        events,
        trace,
        diagnosis,
    )
    return _build_task_diagnosis_report(trace, diagnosis, retry_suggestion)


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict:
    """获取任务详情（内存 → 缓存 → SQLite）"""
    import web.api as _api_module
    _task_planner = getattr(_api_module, "_task_planner", None)
    _task_results = getattr(_api_module, "_task_results", {})

    if task_id in _task_results and _task_results[task_id].get("status") in {
        "completed",
        "failed",
        "cancelled",
        "timeout",
    }:
        cached = dict(_task_results[task_id])
        store = _gs()
        return _attach_persistent_task_metadata(cached, store)

    # 1. TaskPlanner 内存中的任务（含子任务实时状态）
    if _task_planner:
        task = _task_planner.get_task(task_id)
        if task:
            return {
                "id": task.id,
                "task_id": task.id,
                "description": task.description,
                "status": task.status.value,
                "result": task.result,
                "error": task.error,
                "final_score": 0.0,
                "iterations": [],
                "mail_log": "",
                "suggestions": [],
                "shared_dir": "",
                "sub_tasks": [
                    {
                        "id": st.id,
                        "description": st.description,
                        "status": st.status.value,
                        "result": st.result,
                        "error": st.error,
                        "assigned_agent": st.assigned_agent,
                        "acceptance_criteria": st.acceptance_criteria,
                    }
                    for st in task.sub_tasks
                ],
                "created_at": task.created_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
            }

    # 2. 内存缓存
    if task_id in _task_results:
        cached = dict(_task_results[task_id])
        store = _gs()
        return _attach_persistent_task_metadata(cached, store)

    # 3. SQLite 持久化
    store = _gs()
    if store:
        persisted = store.get_task(task_id)
        if persisted:
            return _attach_persistent_task_metadata(persisted, store)

    raise HTTPException(status_code=404, detail="Task not found")


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    """取消正在运行的任务"""
    import web.api as _api_module
    _orchestrator = getattr(_api_module, "_orchestrator", None)

    if not _orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")
    try:
        runner = _get_task_job_runner()
        cancelled = runner.cancel(task_id)
    except HTTPException:
        cancelled = _orchestrator.cancel_task(task_id)
    if cancelled:
        return {"success": True, "message": f"Task {task_id} cancel requested"}
    raise HTTPException(status_code=404, detail="Task not found or not running")


@router.post("/{task_id}/retry")
async def retry_task(task_id: str, request: RetryTaskRequest | None = None) -> dict:
    """重新入队可重试失败任务，或手动恢复失去本地执行器的未完成任务。"""
    runner = _get_task_job_runner()
    try:
        task = runner.retry(task_id, force=request.force if request else False)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    store = _gs()
    return _attach_persistent_task_metadata(dict(task), store)


@router.post("/{task_id}/feedback")
async def submit_task_feedback(task_id: str, request: FeedbackRequest) -> dict:
    """提交任务反馈（Human-in-the-Loop）"""
    import web.api as _api_module
    _orchestrator = getattr(_api_module, "_orchestrator", None)

    if not _orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")
    if not _orchestrator.is_waiting_feedback(task_id):
        raise HTTPException(status_code=404, detail="Task is not waiting for feedback")
    _orchestrator.submit_feedback(task_id, request.feedback)
    return {"success": True}
