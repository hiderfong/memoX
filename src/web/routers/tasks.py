"""Tasks router"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from web.state import get_store as _gs

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskRequest(BaseModel):
    """任务请求"""
    description: str
    context: dict | None = None
    generate_suggestions: bool = True
    active_group_ids: list[str] | None = None
    timeout_seconds: int | None = None  # 任务超时（秒）


class FeedbackRequest(BaseModel):
    feedback: str


@router.post("")
async def create_task(request: TaskRequest) -> dict:
    """创建并执行任务（迭代编排器）"""
    import asyncio

    import web.api as _api_module

    _orchestrator = getattr(_api_module, "_orchestrator", None)
    _task_planner = getattr(_api_module, "_task_planner", None)

    if not _orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")

    timeout = request.timeout_seconds
    try:
        coro = _orchestrator.run(
            description=request.description,
            context=request.context or {},
            active_group_ids=request.active_group_ids,
        )
        if timeout:
            result = await asyncio.wait_for(coro, timeout=float(timeout))
        else:
            result = await coro
    except asyncio.TimeoutError as e:
        raise HTTPException(status_code=504, detail=f"任务执行超时（{timeout}秒）") from e

    suggestions = []
    if request.generate_suggestions and _task_planner:
        from agents.worker_pool import Task

        placeholder_task = Task(
            id=result.task_id,
            description=request.description,
        )
        suggestions = await _task_planner.generate_optimization_suggestions(
            placeholder_task,
            result.result_summary,
            request.context or {},
        )

    # 读取邮件通信日志
    mail_log = ""
    mail_log_path = Path(result.shared_dir) / "mail_log.txt"
    if mail_log_path.exists():
        mail_log = mail_log_path.read_text(encoding="utf-8")

    response_data = {
        "task_id": result.task_id,
        "result": result.result_summary,
        "shared_dir": result.shared_dir,
        "final_score": result.final_score,
        "iterations": [
            {
                "iteration": r.iteration,
                "score": r.score,
                "improvements": r.improvements,
            }
            for r in result.iterations
        ],
        "mail_log": mail_log,
        "suggestions": [
            {
                "type": s.type,
                "title": s.title,
                "description": s.description,
                "confidence": s.confidence,
                "code_snippet": s.code_snippet,
                "priority": s.priority,
            }
            for s in suggestions
        ],
    }

    # 缓存结果供后续查询
    _api_module._task_results[result.task_id] = response_data

    # 检测取消状态
    task_status = "cancelled" if result.result_summary == "(任务已取消)" else "completed"

    # 持久化任务

    store = _gs()
    if store:
        store.save_task({**response_data, "description": request.description, "status": task_status})

    return response_data


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
            return persisted

    # 回退到 TaskPlanner 的内存存储
    if _task_planner:
        return _task_planner.list_tasks()
    return []


@router.get("/running")
async def list_running_tasks() -> list[str]:
    """列出正在运行的任务 ID"""
    import web.api as _api_module
    _orchestrator = getattr(_api_module, "_orchestrator", None)

    if not _orchestrator:
        return []
    return _orchestrator.list_running_tasks()


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


@router.get("/{task_id}")
async def get_task(task_id: str) -> dict:
    """获取任务详情（内存 → 缓存 → SQLite）"""
    import web.api as _api_module
    _task_planner = getattr(_api_module, "_task_planner", None)
    _task_results = getattr(_api_module, "_task_results", {})

    # 1. TaskPlanner 内存中的任务（含子任务实时状态）
    if _task_planner:
        task = _task_planner.get_task(task_id)
        if task:
            return {
                "id": task.id,
                "description": task.description,
                "status": task.status.value,
                "result": task.result,
                "error": task.error,
                "sub_tasks": [
                    {
                        "id": st.id,
                        "description": st.description,
                        "status": st.status.value,
                        "result": st.result,
                        "error": st.error,
                        "assigned_agent": st.assigned_agent,
                    }
                    for st in task.sub_tasks
                ],
                "created_at": task.created_at,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
            }

    # 2. 内存缓存
    if task_id in _task_results:
        return _task_results[task_id]

    # 3. SQLite 持久化
    store = _gs()
    if store:
        persisted = store.get_task(task_id)
        if persisted:
            return persisted

    raise HTTPException(status_code=404, detail="Task not found")


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    """取消正在运行的任务"""
    import web.api as _api_module
    _orchestrator = getattr(_api_module, "_orchestrator", None)

    if not _orchestrator:
        raise HTTPException(status_code=500, detail="Orchestrator not initialized")
    if _orchestrator.cancel_task(task_id):
        return {"success": True, "message": f"Task {task_id} cancel requested"}
    raise HTTPException(status_code=404, detail="Task not found or not running")


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
