"""Scheduled tasks router"""
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from auth import AuthUser, require_role
from web.state import get_store as _gs

router = APIRouter(prefix="/api/scheduled-tasks", tags=["scheduled-tasks"])


class ScheduledTaskCreate(BaseModel):
    description: str
    cron: str
    enabled: bool = True
    active_group_ids: list[str] | None = None
    source_session_id: str | None = None


class ScheduledTaskUpdate(BaseModel):
    description: str | None = None
    cron: str | None = None
    enabled: bool | None = None
    active_group_ids: list[str] | None = None


def _serialize_scheduled(t: dict) -> dict:
    import json as _json

    try:
        gids = _json.loads(t.get("active_group_ids") or "[]")
    except Exception:
        gids = []
    return {**t, "active_group_ids": gids}


@router.get("")
async def list_scheduled_tasks() -> list[dict]:
    """列出所有定时任务"""

    store = _gs()
    if not store:
        return []
    return [_serialize_scheduled(t) for t in store.list_scheduled_tasks()]


@router.post("")
async def create_scheduled_task(
    request: ScheduledTaskCreate,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """创建定时任务（仅管理员）"""
    import uuid
    from datetime import datetime as _dt

    from scheduler import next_run_after, validate_cron

    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    ok, msg = validate_cron(request.cron)
    if not ok:
        raise HTTPException(status_code=400, detail=f"Cron 表达式无效: {msg}")
    description = (request.description or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="任务描述不能为空")

    tid = str(uuid.uuid4())[:12]
    nxt = next_run_after(request.cron, _dt.now())
    next_iso = nxt.isoformat(timespec="minutes") if nxt else ""
    store.create_scheduled_task(
        task_id=tid,
        description=description,
        cron=request.cron,
        active_group_ids=request.active_group_ids or [],
        source_session_id=request.source_session_id or "",
        next_run_at=next_iso,
        enabled=request.enabled,
    )
    return _serialize_scheduled(store.get_scheduled_task(tid))


@router.patch("/{task_id}")
async def update_scheduled_task(
    task_id: str,
    request: ScheduledTaskUpdate,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """更新定时任务（仅管理员）"""
    from datetime import datetime as _dt

    from scheduler import next_run_after, validate_cron

    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    existing = store.get_scheduled_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    if request.cron is not None:
        ok, msg = validate_cron(request.cron)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Cron 表达式无效: {msg}")
    if request.description is not None and not request.description.strip():
        raise HTTPException(status_code=400, detail="任务描述不能为空")

    desc = request.description.strip() if request.description is not None else None
    next_iso: str | None = None
    if request.cron is not None or request.enabled is True:
        use_cron = request.cron if request.cron is not None else existing["cron"]
        nxt = next_run_after(use_cron, _dt.now())
        next_iso = nxt.isoformat(timespec="minutes") if nxt else ""

    store.update_scheduled_task(
        task_id,
        description=desc,
        cron=request.cron,
        enabled=request.enabled,
        active_group_ids=request.active_group_ids,
        next_run_at=next_iso,
    )
    return _serialize_scheduled(store.get_scheduled_task(task_id))


@router.delete("/{task_id}")
async def delete_scheduled_task(
    task_id: str,
    request: Request,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """删除定时任务（仅管理员）"""

    store = _gs()
    if not store:
        raise HTTPException(status_code=500, detail="Storage not initialized")
    existing = store.get_scheduled_task(task_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    store.delete_scheduled_task(task_id)
    return {"success": True, "task_id": task_id}
