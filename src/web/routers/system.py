"""System readiness endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from auth import AuthUser, require_role
from config import default_config_path
from src.ops.archive_mirror import mirror_archive_bytes
from src.ops.backup import BackupError, list_backup_archives, read_backup_metadata, verify_backup
from src.ops.diagnostics import build_diagnostic_bundle
from src.ops.index_consistency import audit_indexes, run_index_repair
from src.ops.readiness import resolve_from_root, run_readiness_checks
from src.ops.recovery import run_restore_drill, run_restore_execute, run_restore_preflight

router = APIRouter(prefix="/api/system", tags=["system"])


class RestoreBackupRequest(BaseModel):
    confirm_archive_name: str
    acknowledge_overwrite: bool = False
    acknowledge_maintenance_mode: bool = False


def _config_path_from_runtime() -> Path:
    path = default_config_path()
    return path if path.is_absolute() else Path.cwd() / path


def _deployment_root() -> Path:
    return _config_path_from_runtime().resolve().parent


def _utc_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _backup_archive_summary(archive: Path) -> dict:
    stat = archive.stat()
    summary = {
        "name": archive.name,
        "archive": str(archive),
        "size_bytes": stat.st_size,
        "modified_at": _utc_from_timestamp(stat.st_mtime),
    }
    try:
        metadata = read_backup_metadata(archive)
    except Exception as exc:
        return {
            **summary,
            "ok": False,
            "status": "error",
            "message": f"Backup metadata unreadable: {type(exc).__name__}: {exc}",
            "metadata_valid": False,
        }

    return {
        **summary,
        "ok": True,
        "status": "ok",
        "message": "Backup metadata is readable",
        "metadata_valid": True,
        "format": metadata.get("format"),
        "created_at": metadata.get("created_at"),
        "included": metadata.get("included", []),
        "missing": metadata.get("missing", []),
        "skipped": metadata.get("skipped", []),
        "entry_count": len(metadata.get("entries", [])),
    }


def _resolve_backup_archive(root: Path, archive_name: str) -> Path:
    if Path(archive_name).name != archive_name:
        raise HTTPException(status_code=404, detail="Backup archive not found")
    if not archive_name.startswith("memox-backup-") or not archive_name.endswith(".tar.gz"):
        raise HTTPException(status_code=404, detail="Backup archive not found")

    backup_dir = (root / "backups").resolve()
    archive = (backup_dir / archive_name).resolve()
    if archive.parent != backup_dir or not archive.is_file():
        raise HTTPException(status_code=404, detail="Backup archive not found")
    return archive


def _record_ops_event(event_type: str, result: dict) -> None:
    try:
        from storage import get_store

        store = get_store()
        if store is None:
            return
        event = store.record_ops_event(
            event_type=event_type,
            status=result.get("status", "error"),
            action=result.get("action", ""),
            message=result.get("message", ""),
            details=result,
        )
        result["event_id"] = event["id"]
        result["recorded_at"] = event["created_at"]
    except Exception:
        return


def _runtime_index_context() -> tuple[object | None, object | None]:
    from web.api import _rag_engine

    vector_store = getattr(_rag_engine, "vector_store", None)
    bm25_indexer = None
    hybrid_retriever = getattr(_rag_engine, "_hybrid_retriever", None)
    if hybrid_retriever is not None:
        bm25_indexer = getattr(hybrid_retriever, "bm25_indexer", None)
    return vector_store, bm25_indexer


def _system_health_report(request: Request) -> dict:
    from web.api import _config, _rag_engine

    config_path = _config_path_from_runtime().resolve()
    root = config_path.parent
    vector_store, bm25_indexer = _runtime_index_context()
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
    latest_restore_drill = None
    latest_restore_execute = None
    latest_index_repair = None
    latest_diagnostics_export = None
    try:
        from storage import get_store

        store = get_store()
        if store is not None:
            latest_event = store.get_latest_ops_event("backup_maintenance")
            latest_restore_drill = store.get_latest_ops_event("restore_drill")
            latest_restore_execute = store.get_latest_ops_event("restore_execute")
            latest_index_repair = store.get_latest_ops_event("index_repair")
            latest_diagnostics_export = store.get_latest_ops_event("diagnostics_export")
    except Exception:
        latest_event = None
        latest_restore_drill = None
        latest_restore_execute = None
        latest_index_repair = None
        latest_diagnostics_export = None

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
        "archive_mirror_enabled": bool(_config.ops.archive_mirror_dir),
        "archive_mirror_dir": _config.ops.archive_mirror_dir,
        "maintenance_runner_active": bool(getattr(maintenance_runner, "running", False)),
        "last_backup_maintenance": latest_event,
        "last_restore_drill": latest_restore_drill,
        "last_restore_execute": latest_restore_execute,
        "last_index_repair": latest_index_repair,
        "last_diagnostics_export": latest_diagnostics_export,
    }
    return result


def _index_audit_report() -> dict:
    from web.api import _config

    config_path = _config_path_from_runtime().resolve()
    root = config_path.parent
    vector_store, bm25_indexer = _runtime_index_context()
    if vector_store is None or bm25_indexer is None:
        return {"ok": False, "status": "error", "message": "RAG runtime is not loaded"}
    manifest_path = resolve_from_root(root, _config.knowledge_base.manifest_path)
    return audit_indexes(
        vector_store=vector_store,
        bm25_indexer=bm25_indexer,
        manifest_path=manifest_path,
        collection_name="documents",
    )


@router.get("/health")
async def system_health(
    request: Request,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Return an authenticated operational readiness report."""
    return _system_health_report(request)


@router.get("/backups")
async def list_system_backups(
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Return local backup archive metadata for administrators."""
    root = _deployment_root()
    archives = list_backup_archives(root)
    return {
        "root": str(root),
        "backup_dir": str(root / "backups"),
        "count": len(archives),
        "backups": [_backup_archive_summary(archive) for archive in archives],
    }


@router.get("/diagnostics/export")
async def export_system_diagnostics(
    request: Request,
    _: Annotated[AuthUser, require_role("admin")],
) -> Response:
    """Export a zip bundle with structured operational diagnostics."""
    from storage import get_store
    from web.api import _config

    root = _deployment_root()
    store = get_store()
    events = store.list_ops_events(limit=50) if store is not None else []
    archives = list_backup_archives(root)
    payload = {
        "root": str(root),
        "backup_dir": str(root / "backups"),
        "count": len(archives),
        "backups": [_backup_archive_summary(archive) for archive in archives],
    }
    bundle, filename, details = await asyncio.to_thread(
        build_diagnostic_bundle,
        root=root,
        config_path=_config_path_from_runtime().resolve(),
        config=_config,
        system_health=_system_health_report(request),
        backups=payload,
        ops_events={"count": len(events), "events": events},
        index_report=_index_audit_report(),
    )
    mirror = await asyncio.to_thread(
        mirror_archive_bytes,
        bundle,
        filename,
        root=root,
        mirror_dir=_config.ops.archive_mirror_dir,
        category="diagnostics",
    )
    details["mirror"] = mirror
    mirror_warning = mirror["enabled"] and (not mirror["ok"] or mirror["status"] == "warning")
    if mirror_warning:
        details["status"] = "warning"
        details["message"] = f"{details.get('message', 'Diagnostic bundle exported')}; {mirror['message']}"
    _record_ops_event("diagnostics_export", details)
    return Response(
        content=bundle,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/events")
async def list_system_events(
    _: Annotated[AuthUser, require_role("admin")],
    event_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Return recent operational events for administrators."""
    try:
        from storage import get_store

        store = get_store()
        events = store.list_ops_events(event_type=event_type, limit=limit) if store is not None else []
    except Exception:
        events = []
    return {
        "event_type": event_type,
        "limit": limit,
        "count": len(events),
        "events": events,
    }


@router.post("/backups/{archive_name}/verify")
async def verify_system_backup(
    archive_name: str,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Verify a local backup archive without restoring it."""
    archive = _resolve_backup_archive(_deployment_root(), archive_name)
    try:
        result = await asyncio.to_thread(verify_backup, archive)
    except BackupError as exc:
        return {
            **_backup_archive_summary(archive),
            "verified": False,
            "ok": False,
            "status": "error",
            "message": str(exc),
        }
    except Exception as exc:
        return {
            **_backup_archive_summary(archive),
            "verified": False,
            "ok": False,
            "status": "error",
            "message": f"Backup verification failed: {type(exc).__name__}: {exc}",
        }

    return {
        "name": archive.name,
        "archive": result["archive"],
        "ok": True,
        "status": "ok",
        "message": "Backup archive verified",
        "verified": True,
        "created_at": result.get("created_at"),
        "included": result.get("included", []),
        "missing": result.get("missing", []),
        "skipped": result.get("skipped", []),
        "entry_count": len(result.get("entries", [])),
    }


@router.post("/backups/{archive_name}/restore-drill")
async def run_system_backup_restore_drill(
    archive_name: str,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Restore a local backup archive into a disposable directory and check key paths."""
    archive = _resolve_backup_archive(_deployment_root(), archive_name)
    result = await asyncio.to_thread(run_restore_drill, archive)
    _record_ops_event("restore_drill", result)
    return result


@router.post("/backups/{archive_name}/restore-preflight")
async def run_system_backup_restore_preflight(
    archive_name: str,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Analyze a restore against the current deployment root without writing files."""
    archive = _resolve_backup_archive(_deployment_root(), archive_name)
    result = await asyncio.to_thread(run_restore_preflight, archive, _deployment_root())
    _record_ops_event("restore_preflight", result)
    return result


@router.post("/backups/{archive_name}/restore")
async def run_system_backup_restore(
    archive_name: str,
    request: RestoreBackupRequest,
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Restore a local backup archive into the current deployment root after explicit confirmations."""
    from web.api import _config

    archive = _resolve_backup_archive(_deployment_root(), archive_name)
    include = tuple(_config.ops.auto_backup_include) if _config is not None else ("config.yaml", "data", "workspace")
    result = await asyncio.to_thread(
        run_restore_execute,
        archive,
        _deployment_root(),
        confirm_archive_name=request.confirm_archive_name,
        acknowledge_overwrite=request.acknowledge_overwrite,
        acknowledge_maintenance_mode=request.acknowledge_maintenance_mode,
        safety_include=include,
    )
    _record_ops_event("restore_execute", result)
    return result


@router.post("/indexes/repair")
async def run_system_index_repair(
    _: Annotated[AuthUser, require_role("admin")],
    collection: str = Query(default="documents", min_length=1, max_length=64),
) -> dict:
    """Repair disk-backed Chroma/BM25/manifest consistency for the configured collection."""
    result = await asyncio.to_thread(run_index_repair, _config_path_from_runtime().resolve(), collection)
    _record_ops_event("index_repair", result)
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
        archive_mirror_dir=_config.ops.archive_mirror_dir,
    )
    record_maintenance_event(get_store(), result)
    return result
