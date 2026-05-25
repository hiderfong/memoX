"""Tasks router"""
import contextlib
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from coordinator.task_jobs import TaskJobRequest, TaskJobRunner
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
