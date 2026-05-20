"""System readiness endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Request

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
    return result
