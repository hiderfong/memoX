"""Workflows router"""
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from workflow import (
    WorkflowEngine,
    WorkflowPersistence,
    parse_workflow_yaml,
)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

_workflow_engine: WorkflowEngine | None = None
_workflow_persistence: WorkflowPersistence | None = None


def get_workflow_engine() -> WorkflowEngine:
    global _workflow_engine, _workflow_persistence
    if _workflow_engine is None:
        from agents.worker_pool import get_worker_pool
        from agents.base_agent import create_provider

        worker_pool = get_worker_pool()
        provider = create_provider()
        _workflow_persistence = WorkflowPersistence()
        _workflow_engine = WorkflowEngine(worker_pool, provider, _workflow_persistence)
    return _workflow_engine


def _get_api_globals():
    """Access workflow globals from api module (initialized in startup)."""
    import web.api as _api_module
    return (
        getattr(_api_module, "_workflow_engine"),
        getattr(_api_module, "_workflow_persistence"),
    )


@router.post("/validate")
async def validate_workflow(yaml_content: str) -> dict:
    """验证工作流 YAML 语法和 DAG 合法性"""
    try:
        wf = parse_workflow_yaml(yaml_content)
        errors = wf.validate()
        return {"valid": len(errors) == 0, "errors": errors, "step_count": len(wf.steps)}
    except Exception as e:
        return {"valid": False, "errors": [str(e)], "step_count": 0}


@router.post("/run")
async def run_workflow(
    yaml_content: str,
    context: dict | None = None,
) -> dict:
    """执行工作流（YAML 内容）"""
    try:
        wf = parse_workflow_yaml(yaml_content)
        # Use api module's engine if available (initialized in startup), otherwise lazy init
        _wf_engine, _wf_persist = _get_api_globals()
        engine = _wf_engine if _wf_engine else get_workflow_engine()
        run = await engine.execute(wf, context=context or {})
        return {
            "run_id": run.id,
            "status": run.status.value,
            "context_keys": list(run.context.keys()),
            "step_count": len(run.step_records),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/runs")
async def list_workflow_runs(workflow_name: str | None = None, limit: int = 50) -> list[dict]:
    """列出工作流运行记录"""
    _wf_engine, _wf_persist = _get_api_globals()
    persistence = _wf_persist if _wf_persist else _workflow_persistence
    if persistence is None:
        persistence = WorkflowPersistence()
    runs = persistence.list_runs(workflow_name=workflow_name, limit=limit)
    return [
        {
            "id": r.id,
            "workflow_name": r.workflow_name,
            "status": r.status.value,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
            "step_count": len(r.step_records),
        }
        for r in runs
    ]


@router.get("/runs/{run_id}")
async def get_workflow_run(run_id: str) -> dict:
    """获取工作流运行详情"""
    _wf_engine, _wf_persist = _get_api_globals()
    persistence = _wf_persist if _wf_persist else _workflow_persistence
    if persistence is None:
        persistence = WorkflowPersistence()
    run = persistence.load_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {
        "id": run.id,
        "workflow_name": run.workflow_name,
        "status": run.status.value,
        "context": run.context,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "paused_at": run.paused_at,
        "completed_at": run.completed_at,
        "steps": [
            {
                "step_id": r.step_id,
                "status": r.status.value,
                "output": r.output,
                "error": r.error,
                "duration_ms": r.duration_ms,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
            }
            for r in run.step_records
        ],
    }


@router.post("/runs/{run_id}/pause")
async def pause_workflow_run(run_id: str) -> dict:
    """暂停工作流运行"""
    _wf_engine, _wf_persist = _get_api_globals()
    engine = _wf_engine if _wf_engine else get_workflow_engine()
    run = await engine.pause(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"run_id": run.id, "status": run.status.value}


@router.post("/runs/{run_id}/resume")
async def resume_workflow_run(run_id: str, yaml_content: str, context: dict | None = None) -> dict:
    """恢复暂停的工作流"""
    try:
        wf = parse_workflow_yaml(yaml_content)
        _wf_engine, _wf_persist = _get_api_globals()
        engine = _wf_engine if _wf_engine else get_workflow_engine()
        run = await engine.resume(run_id, wf, context=context)
        return {"run_id": run.id, "status": run.status.value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/runs/{run_id}")
async def delete_workflow_run(run_id: str) -> dict:
    """删除工作流运行记录"""
    _wf_engine, _wf_persist = _get_api_globals()
    persistence = _wf_persist if _wf_persist else _workflow_persistence
    if persistence is None:
        persistence = WorkflowPersistence()
    persistence._delete_run(run_id)
    return {"deleted": run_id}
