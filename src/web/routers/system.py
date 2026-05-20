"""System readiness endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Request

from auth import AuthUser, require_role
from config import default_config_path
from src.ops.readiness import run_readiness_checks

router = APIRouter(prefix="/api/system", tags=["system"])


def _config_path_from_runtime() -> Path:
    path = default_config_path()
    return path if path.is_absolute() else Path.cwd() / path


@router.get("/health")
async def system_health(
    request: Request,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Return an authenticated operational readiness report."""
    from web.api import _config, _rag_engine

    config_path = _config_path_from_runtime().resolve()
    root = config_path.parent
    vector_store = getattr(_rag_engine, "vector_store", None)
    bm25_indexer = None
    hybrid_retriever = getattr(_rag_engine, "_hybrid_retriever", None)
    if hybrid_retriever is not None:
        bm25_indexer = getattr(hybrid_retriever, "bm25_indexer", None)

    result = run_readiness_checks(
        root=root,
        config_path=config_path,
        config=_config,
        vector_store=vector_store,
        bm25_indexer=bm25_indexer,
        include_backup=True,
        max_backup_age_hours=_config.ops.auto_backup_interval_hours,
        max_backups=_config.ops.max_backups,
    )
    result["runtime"] = {
        "app": getattr(request.app, "title", "MemoX API"),
        "version": getattr(request.app, "version", ""),
        "config_loaded": _config is not None,
        "rag_engine_loaded": _rag_engine is not None,
    }
    store = None
    latest_event = None
    try:
        from storage import get_store

        store = get_store()
        if store is not None:
            latest_event = store.get_latest_ops_event("backup_maintenance")
    except Exception:
        latest_event = None

    try:
        from ops.maintenance import get_maintenance_runner

        maintenance_runner = get_maintenance_runner()
    except Exception:
        maintenance_runner = None

    result["ops"] = {
        "auto_backup_enabled": _config.ops.auto_backup_enabled,
        "auto_backup_interval_hours": _config.ops.auto_backup_interval_hours,
        "auto_backup_startup_delay_seconds": _config.ops.auto_backup_startup_delay_seconds,
        "max_backups": _config.ops.max_backups,
        "maintenance_runner_active": bool(getattr(maintenance_runner, "running", False)),
        "last_backup_maintenance": latest_event,
    }
    return result


@router.post("/maintenance/backup")
async def run_backup_maintenance_now(
    _: Annotated[AuthUser, require_role("admin")],
    force: bool = Query(default=True),
) -> dict:
    """Run backup maintenance on demand for administrators."""
    from ops.maintenance import get_maintenance_runner, record_maintenance_event, run_backup_maintenance
    from storage import get_store
    from web.api import _config

    runner = get_maintenance_runner()
    if runner is not None:
        return await runner.run_once(force=force)

    config_path = _config_path_from_runtime().resolve()
    result = await asyncio.to_thread(
        run_backup_maintenance,
        root=config_path.parent,
        include=tuple(_config.ops.auto_backup_include),
        interval_hours=_config.ops.auto_backup_interval_hours,
        max_backups=_config.ops.max_backups,
        force=force,
    )
    record_maintenance_event(get_store(), result)
    return result
