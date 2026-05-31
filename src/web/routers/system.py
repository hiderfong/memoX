"""System readiness endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth import AuthUser, require_role
from config import Config, ToolPolicyConfig, default_config_path, validate_config
from src.ops.archive_mirror import mirror_archive_bytes
from src.ops.backup import BackupError, list_backup_archives, read_backup_metadata, verify_backup
from src.ops.diagnostics import build_diagnostic_bundle
from src.ops.index_consistency import audit_indexes, run_index_repair
from src.ops.lifecycle import LifecyclePolicy, run_lifecycle_cleanup
from src.ops.readiness import resolve_from_root, run_readiness_checks
from src.ops.recovery import run_restore_drill, run_restore_execute, run_restore_preflight
from src.ops.redaction import REDACTED

router = APIRouter(prefix="/api/system", tags=["system"])


class RestoreBackupRequest(BaseModel):
    confirm_archive_name: str
    acknowledge_overwrite: bool = False
    acknowledge_maintenance_mode: bool = False


class NetworkToolPolicyRequest(BaseModel):
    allow_internal_hosts: list[str] = Field(default_factory=list, max_length=100)


class PlaywrightCrawlerPolicyRequest(BaseModel):
    max_concurrency: int = Field(default=2, ge=1, le=20)
    queue_timeout_seconds: float = Field(default=10.0, ge=0, le=300)
    total_timeout_seconds: float = Field(default=45.0, ge=1, le=600)
    navigation_timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    selector_timeout_ms: int = Field(default=10000, ge=0, le=300000)
    idle_wait_ms: int = Field(default=2000, ge=0, le=60000)
    max_pages: int = Field(default=1, ge=1, le=10)
    max_response_bytes: int = Field(default=5_000_000, ge=1024, le=100_000_000)
    max_output_chars: int = Field(default=8000, ge=100, le=200000)


class WebToolPolicyRequest(BaseModel):
    request_timeout_seconds: float = Field(default=15.0, ge=1, le=300)
    max_response_bytes: int = Field(default=2_000_000, ge=1024, le=100_000_000)
    max_fetch_chars: int = Field(default=20_000, ge=100, le=500_000)
    max_search_results: int = Field(default=10, ge=1, le=50)


class DatabaseToolPolicyDataSourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    connection_string: str = Field(min_length=1, max_length=2048)
    redacted: bool = False


class DatabaseToolPolicyRequest(BaseModel):
    default_access_mode: Literal["read_only", "write", "admin"] = "read_only"
    allow_raw_connection_strings: bool = True
    allow_write: bool = True
    allow_ddl: bool = False
    allow_multiple_statements: bool = False
    max_result_rows: int = Field(default=200, ge=1, le=10000)
    data_sources: list[DatabaseToolPolicyDataSourceRequest] = Field(default_factory=list, max_length=100)


class ToolPolicyUpdateRequest(BaseModel):
    network: NetworkToolPolicyRequest = Field(default_factory=NetworkToolPolicyRequest)
    web: WebToolPolicyRequest = Field(default_factory=WebToolPolicyRequest)
    playwright_crawler: PlaywrightCrawlerPolicyRequest = Field(default_factory=PlaywrightCrawlerPolicyRequest)
    database: DatabaseToolPolicyRequest = Field(default_factory=DatabaseToolPolicyRequest)


def _config_path_from_runtime() -> Path:
    path = default_config_path()
    return path if path.is_absolute() else Path.cwd() / path


def _deployment_root() -> Path:
    return _config_path_from_runtime().resolve().parent


def _utc_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _read_config_document(config_path: Path) -> dict:
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=f"配置文件不存在: {config_path}") from exc
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=500, detail=f"配置文件 YAML 无法解析: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="配置文件顶层必须是 YAML mapping")
    return data


def _section_span(text: str, key: str) -> tuple[int, int] | None:
    lines = text.splitlines(keepends=True)
    start_line: int | None = None
    for index, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}:\s*(?:#.*)?$", line.rstrip("\n")):
            start_line = index
            break
    if start_line is None:
        return None

    end_line = len(lines)
    for index in range(start_line + 1, len(lines)):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*(?:#.*)?$", lines[index].rstrip("\n")):
            end_line = index
            break

    while end_line > start_line + 1:
        previous = lines[end_line - 1].strip()
        if previous and not previous.startswith("#"):
            break
        end_line -= 1

    start = sum(len(line) for line in lines[:start_line])
    end = sum(len(line) for line in lines[:end_line])
    return start, end


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _replace_top_level_yaml_section(text: str, key: str, payload: dict) -> str:
    new_section = yaml.safe_dump(
        {key: payload},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()
    span = _section_span(text, key)
    if span is None:
        suffix = "" if text.endswith("\n") or not text else "\n"
        return f"{text}{suffix}\n{new_section}\n"
    start, end = span
    return f"{text[:start]}{new_section}\n\n{text[end:].lstrip()}"


def _validate_internal_host_allowlist(hosts: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw_host in hosts:
        host = str(raw_host).strip()
        if not host:
            continue
        if any(ch.isspace() for ch in host) or "/" in host:
            raise HTTPException(status_code=400, detail=f"allow_internal_hosts 条目无效: {raw_host}")
        if host not in seen:
            cleaned.append(host)
            seen.add(host)
    return cleaned


def _mask_connection_string(connection_string: str) -> tuple[str, bool]:
    value = str(connection_string)
    if value.startswith("${") and value.endswith("}"):
        return value, False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return REDACTED, True
    if parsed.scheme.startswith("sqlite"):
        return value, False
    if parsed.password or parsed.username:
        host = parsed.hostname or ""
        try:
            port_number = parsed.port
        except ValueError:
            port_number = None
        port = f":{port_number}" if port_number else ""
        netloc = f"{REDACTED}@{host}{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", "")), True
    if parsed.query or parsed.fragment:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")), True
    return value, False


def _tool_policy_payload(policy: ToolPolicyConfig) -> dict:
    data_sources = []
    for name, connection_string in sorted(policy.database.data_sources.items()):
        display_value, redacted = _mask_connection_string(connection_string)
        data_sources.append({
            "name": name,
            "connection_string": display_value,
            "redacted": redacted,
        })
    return {
        "network": {
            "allow_internal_hosts": list(policy.network.allow_internal_hosts),
        },
        "web": {
            "request_timeout_seconds": policy.web.request_timeout_seconds,
            "max_response_bytes": policy.web.max_response_bytes,
            "max_fetch_chars": policy.web.max_fetch_chars,
            "max_search_results": policy.web.max_search_results,
        },
        "playwright_crawler": {
            "max_concurrency": policy.playwright_crawler.max_concurrency,
            "queue_timeout_seconds": policy.playwright_crawler.queue_timeout_seconds,
            "total_timeout_seconds": policy.playwright_crawler.total_timeout_seconds,
            "navigation_timeout_ms": policy.playwright_crawler.navigation_timeout_ms,
            "selector_timeout_ms": policy.playwright_crawler.selector_timeout_ms,
            "idle_wait_ms": policy.playwright_crawler.idle_wait_ms,
            "max_pages": policy.playwright_crawler.max_pages,
            "max_response_bytes": policy.playwright_crawler.max_response_bytes,
            "max_output_chars": policy.playwright_crawler.max_output_chars,
        },
        "database": {
            "default_access_mode": policy.database.default_access_mode,
            "allow_raw_connection_strings": policy.database.allow_raw_connection_strings,
            "allow_write": policy.database.allow_write,
            "allow_ddl": policy.database.allow_ddl,
            "allow_multiple_statements": policy.database.allow_multiple_statements,
            "max_result_rows": policy.database.max_result_rows,
            "data_sources": data_sources,
        },
    }


def _tool_policy_section_from_request(body: ToolPolicyUpdateRequest, existing: dict) -> dict:
    existing_sources = existing.get("database", {}).get("data_sources", {})
    if not isinstance(existing_sources, dict):
        existing_sources = {}

    data_sources: dict[str, str] = {}
    for source in body.database.data_sources:
        name = source.name.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*$", name):
            raise HTTPException(status_code=400, detail=f"数据源名称无效: {source.name}")
        connection_string = source.connection_string.strip()
        if source.redacted and REDACTED in connection_string:
            preserved = existing_sources.get(name)
            if not preserved:
                raise HTTPException(status_code=400, detail=f"无法保留不存在的数据源: {name}")
            data_sources[name] = str(preserved)
        else:
            data_sources[name] = connection_string

    return {
        "network": {
            "allow_internal_hosts": _validate_internal_host_allowlist(body.network.allow_internal_hosts),
        },
        "web": {
            "request_timeout_seconds": body.web.request_timeout_seconds,
            "max_response_bytes": body.web.max_response_bytes,
            "max_fetch_chars": body.web.max_fetch_chars,
            "max_search_results": body.web.max_search_results,
        },
        "playwright_crawler": {
            "max_concurrency": body.playwright_crawler.max_concurrency,
            "queue_timeout_seconds": body.playwright_crawler.queue_timeout_seconds,
            "total_timeout_seconds": body.playwright_crawler.total_timeout_seconds,
            "navigation_timeout_ms": body.playwright_crawler.navigation_timeout_ms,
            "selector_timeout_ms": body.playwright_crawler.selector_timeout_ms,
            "idle_wait_ms": body.playwright_crawler.idle_wait_ms,
            "max_pages": body.playwright_crawler.max_pages,
            "max_response_bytes": body.playwright_crawler.max_response_bytes,
            "max_output_chars": body.playwright_crawler.max_output_chars,
        },
        "database": {
            "default_access_mode": body.database.default_access_mode,
            "allow_raw_connection_strings": body.database.allow_raw_connection_strings,
            "allow_write": body.database.allow_write,
            "allow_ddl": body.database.allow_ddl,
            "allow_multiple_statements": body.database.allow_multiple_statements,
            "max_result_rows": body.database.max_result_rows,
            "data_sources": data_sources,
        },
    }


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


def _actor_payload(user: AuthUser | None) -> dict[str, str] | None:
    if user is None:
        return None
    return {
        "username": user.username,
        "role": user.role,
        "display_name": user.display_name,
    }


def _record_ops_event(event_type: str, result: dict, actor: AuthUser | None = None) -> None:
    try:
        from storage import get_store

        store = get_store()
        if store is None:
            return
        actor_details = _actor_payload(actor)
        if actor_details:
            result["actor"] = actor_details
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


def _lifecycle_policy_from_config(config) -> LifecyclePolicy:
    return LifecyclePolicy(
        ops_event_retention_days=config.ops.ops_event_retention_days,
        audit_log_retention_days=config.ops.audit_log_retention_days,
        task_job_retention_days=config.ops.task_job_retention_days,
        diagnostic_retention_days=config.ops.diagnostic_retention_days,
        max_diagnostic_bundles=config.ops.max_diagnostic_bundles,
    )


def _refresh_readiness_status(result: dict) -> None:
    statuses = {check.get("status") for check in result.get("checks", [])}
    if "error" in statuses:
        status = "error"
    elif "warning" in statuses:
        status = "warning"
    else:
        status = "ok"
    result["status"] = status
    result["ok"] = status != "error"


def _knowledge_graph_quality_snapshot_summary(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else snapshot
    return {
        "id": snapshot.get("id"),
        "created_at": snapshot.get("created_at"),
        "health_score": metrics.get("health_score"),
        "risk_level": metrics.get("risk_level"),
        "relation_count": metrics.get("relation_count"),
        "low_confidence_ratio": metrics.get("low_confidence_ratio"),
        "isolated_relation_ratio": metrics.get("isolated_relation_ratio"),
        "open_review_backlog_ratio": metrics.get("open_review_backlog_ratio", metrics.get("review_backlog_ratio")),
        "trigger": metrics.get("trigger") if isinstance(metrics.get("trigger"), dict) else None,
        "quality_gate": metrics.get("quality_gate") if isinstance(metrics.get("quality_gate"), dict) else None,
    }


def _knowledge_graph_quality_gate_report(config, snapshot: dict | None) -> dict:
    from knowledge.knowledge_graph import evaluate_knowledge_graph_quality_gate

    if not config.knowledge_base.enable_graph:
        gate = evaluate_knowledge_graph_quality_gate({}, {
            "enabled": False,
            "min_health_score": config.knowledge_base.graph_quality_gate.min_health_score,
            "max_low_confidence_ratio": config.knowledge_base.graph_quality_gate.max_low_confidence_ratio,
            "max_isolated_relation_ratio": config.knowledge_base.graph_quality_gate.max_isolated_relation_ratio,
            "max_open_review_backlog_ratio": config.knowledge_base.graph_quality_gate.max_open_review_backlog_ratio,
            "require_relations": config.knowledge_base.graph_quality_gate.require_relations,
            "min_relation_count": config.knowledge_base.graph_quality_gate.min_relation_count,
        })
        gate["message"] = "知识图谱未启用。"
        return gate

    metrics = snapshot.get("metrics") if snapshot and isinstance(snapshot.get("metrics"), dict) else (snapshot or {})
    return evaluate_knowledge_graph_quality_gate(metrics, config.knowledge_base.graph_quality_gate)


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
    latest_lifecycle_cleanup = None
    latest_knowledge_graph_quality_alert = None
    latest_knowledge_graph_governance_task = None
    latest_knowledge_graph_quality_snapshot = None
    task_job_stats = None
    try:
        from storage import get_store

        store = get_store()
        if store is not None:
            latest_event = store.get_latest_ops_event("backup_maintenance")
            latest_restore_drill = store.get_latest_ops_event("restore_drill")
            latest_restore_execute = store.get_latest_ops_event("restore_execute")
            latest_index_repair = store.get_latest_ops_event("index_repair")
            latest_diagnostics_export = store.get_latest_ops_event("diagnostics_export")
            latest_lifecycle_cleanup = store.get_latest_ops_event("lifecycle_cleanup")
            latest_knowledge_graph_quality_alert = store.get_latest_ops_event("knowledge_graph_quality_alert")
            latest_knowledge_graph_governance_task = store.get_latest_ops_event("knowledge_graph_governance_task")
            quality_snapshots = store.list_knowledge_graph_quality_snapshots(limit=1)
            latest_knowledge_graph_quality_snapshot = quality_snapshots[-1] if quality_snapshots else None
            task_job_stats = store.get_task_job_stats()
    except Exception:
        latest_event = None
        latest_restore_drill = None
        latest_restore_execute = None
        latest_index_repair = None
        latest_diagnostics_export = None
        latest_lifecycle_cleanup = None
        latest_knowledge_graph_quality_alert = None
        latest_knowledge_graph_governance_task = None
        latest_knowledge_graph_quality_snapshot = None

    try:
        from ops.maintenance import get_maintenance_runner

        maintenance_runner = get_maintenance_runner()
    except Exception:
        maintenance_runner = None

    knowledge_graph_quality_gate = _knowledge_graph_quality_gate_report(_config, latest_knowledge_graph_quality_snapshot)
    knowledge_graph_quality_snapshot = _knowledge_graph_quality_snapshot_summary(latest_knowledge_graph_quality_snapshot)
    result.setdefault("checks", []).append({
        "name": "knowledge_graph_quality_gate",
        "status": knowledge_graph_quality_gate["status"],
        "message": knowledge_graph_quality_gate["message"],
        "details": {
            "gate": knowledge_graph_quality_gate,
            "latest_snapshot": knowledge_graph_quality_snapshot,
        },
        "duration_ms": 0,
    })
    _refresh_readiness_status(result)

    result["ops"] = {
        "auto_backup_enabled": _config.ops.auto_backup_enabled,
        "auto_backup_interval_hours": _config.ops.auto_backup_interval_hours,
        "auto_backup_startup_delay_seconds": _config.ops.auto_backup_startup_delay_seconds,
        "max_backups": _config.ops.max_backups,
        "archive_mirror_enabled": bool(_config.ops.archive_mirror_dir),
        "archive_mirror_dir": _config.ops.archive_mirror_dir,
        "retention": {
            "ops_event_retention_days": _config.ops.ops_event_retention_days,
            "audit_log_retention_days": _config.ops.audit_log_retention_days,
            "task_job_retention_days": _config.ops.task_job_retention_days,
            "diagnostic_retention_days": _config.ops.diagnostic_retention_days,
            "max_diagnostic_bundles": _config.ops.max_diagnostic_bundles,
        },
        "task_jobs": task_job_stats,
        "maintenance_runner_active": bool(getattr(maintenance_runner, "running", False)),
        "last_backup_maintenance": latest_event,
        "last_restore_drill": latest_restore_drill,
        "last_restore_execute": latest_restore_execute,
        "last_index_repair": latest_index_repair,
        "last_diagnostics_export": latest_diagnostics_export,
        "last_lifecycle_cleanup": latest_lifecycle_cleanup,
        "last_knowledge_graph_quality_alert": latest_knowledge_graph_quality_alert,
        "last_knowledge_graph_governance_task": latest_knowledge_graph_governance_task,
        "last_knowledge_graph_quality_snapshot": knowledge_graph_quality_snapshot,
        "knowledge_graph_quality_gate": knowledge_graph_quality_gate,
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
    _: Annotated[AuthUser, require_role("admin", "monitor")],
) -> dict:
    """Return an authenticated operational readiness report."""
    return _system_health_report(request)


@router.get("/tool-policy")
async def get_system_tool_policy(
    _: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Return the current high-permission tool policy without exposing secrets."""
    from web.api import _config

    if _config is None:
        raise HTTPException(status_code=500, detail="Config not available")
    return _tool_policy_payload(_config.tool_policy)


@router.put("/tool-policy")
async def update_system_tool_policy(
    body: ToolPolicyUpdateRequest,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Persist and apply the high-permission tool policy."""
    import config as config_module
    import web.api as api_module

    config_path = _config_path_from_runtime().resolve()
    data = _read_config_document(config_path)
    existing_policy = data.get("tool_policy", {})
    if not isinstance(existing_policy, dict):
        existing_policy = {}
    tool_policy_section = _tool_policy_section_from_request(body, existing_policy)
    data["tool_policy"] = tool_policy_section

    try:
        candidate = Config._from_dict(data)
        validate_config(candidate)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"工具策略配置无效: {exc}") from exc

    original_text = config_path.read_text(encoding="utf-8")
    _atomic_write_text(config_path, _replace_top_level_yaml_section(original_text, "tool_policy", tool_policy_section))

    if api_module._config is not None:
        api_module._config.tool_policy = candidate.tool_policy
    applied_config = api_module._config or candidate
    config_module._config = applied_config
    with contextlib.suppress(Exception):
        import src.config as src_config_module

        src_config_module._config = applied_config

    with contextlib.suppress(Exception):
        from storage import get_store

        store = get_store()
        if store:
            store.log_audit_event(
                action="update",
                resource="tool_policy",
                resource_id="tool_policy",
                username=user.username,
                user_role=user.role,
                details=_tool_policy_payload(candidate.tool_policy),
            )

    return {
        "success": True,
        "message": "工具策略已保存并应用",
        "tool_policy": _tool_policy_payload(candidate.tool_policy),
    }


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
    user: Annotated[AuthUser, require_role("admin")],
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
    _record_ops_event("diagnostics_export", details, user)
    return Response(
        content=bundle,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/events")
async def list_system_events(
    _: Annotated[AuthUser, require_role("admin", "monitor")],
    event_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Return recent operational events for administrators."""
    try:
        from storage import get_store

        store = get_store()
        if store is not None:
            events = store.list_ops_events(event_type=event_type, status=status, limit=limit, offset=offset)
            total = store.count_ops_events(event_type=event_type, status=status)
        else:
            events = []
            total = 0
    except Exception:
        events = []
        total = 0
    return {
        "event_type": event_type,
        "status": status,
        "limit": limit,
        "offset": offset,
        "count": len(events),
        "total": total,
        "events": events,
    }


@router.get("/tool-audit")
async def list_system_tool_audit(
    _: Annotated[AuthUser, require_role("admin", "monitor")],
    tool_name: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(success|error|rejected)$"),
    worker_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    timestamp_from: str | None = Query(default=None),
    timestamp_to: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Return audited tool calls for administrators."""
    try:
        from storage import get_store

        store = get_store()
        if store is not None:
            events = store.list_audit_events(
                resource="tool",
                action="tool_call",
                resource_id=tool_name,
                status=status,
                worker_id=worker_id,
                task_id=task_id,
                timestamp_from=timestamp_from,
                timestamp_to=timestamp_to,
                limit=limit,
                offset=offset,
            )
            total = store.count_audit_events(
                resource="tool",
                action="tool_call",
                resource_id=tool_name,
                status=status,
                worker_id=worker_id,
                task_id=task_id,
                timestamp_from=timestamp_from,
                timestamp_to=timestamp_to,
            )
            summary = {
                item_status: store.count_audit_events(
                    resource="tool",
                    action="tool_call",
                    resource_id=tool_name,
                    status=item_status,
                    worker_id=worker_id,
                    task_id=task_id,
                    timestamp_from=timestamp_from,
                    timestamp_to=timestamp_to,
                )
                for item_status in ("success", "rejected", "error")
            }
        else:
            events = []
            total = 0
            summary = {"success": 0, "rejected": 0, "error": 0}
    except Exception:
        events = []
        total = 0
        summary = {"success": 0, "rejected": 0, "error": 0}

    return {
        "tool_name": tool_name,
        "status": status,
        "worker_id": worker_id,
        "task_id": task_id,
        "timestamp_from": timestamp_from,
        "timestamp_to": timestamp_to,
        "limit": limit,
        "offset": offset,
        "count": len(events),
        "total": total,
        "summary": summary,
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
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Restore a local backup archive into a disposable directory and check key paths."""
    archive = _resolve_backup_archive(_deployment_root(), archive_name)
    result = await asyncio.to_thread(run_restore_drill, archive)
    _record_ops_event("restore_drill", result, user)
    return result


@router.post("/backups/{archive_name}/restore-preflight")
async def run_system_backup_restore_preflight(
    archive_name: str,
    user: Annotated[AuthUser, require_role("admin")],
) -> dict:
    """Analyze a restore against the current deployment root without writing files."""
    archive = _resolve_backup_archive(_deployment_root(), archive_name)
    result = await asyncio.to_thread(run_restore_preflight, archive, _deployment_root())
    _record_ops_event("restore_preflight", result, user)
    return result


@router.post("/backups/{archive_name}/restore")
async def run_system_backup_restore(
    archive_name: str,
    request: RestoreBackupRequest,
    user: Annotated[AuthUser, require_role("admin")],
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
    _record_ops_event("restore_execute", result, user)
    return result


@router.post("/indexes/repair")
async def run_system_index_repair(
    user: Annotated[AuthUser, require_role("admin")],
    collection: str = Query(default="documents", min_length=1, max_length=64),
) -> dict:
    """Repair disk-backed Chroma/BM25/manifest consistency for the configured collection."""
    result = await asyncio.to_thread(run_index_repair, _config_path_from_runtime().resolve(), collection)
    _record_ops_event("index_repair", result, user)
    return result


@router.post("/maintenance/backup")
async def run_backup_maintenance_now(
    user: Annotated[AuthUser, require_role("admin")],
    force: bool = Query(default=True),
) -> dict:
    """Run backup maintenance on demand for administrators."""
    from ops.maintenance import get_maintenance_runner, record_maintenance_event, run_backup_maintenance
    from storage import get_store
    from web.api import _config

    runner = get_maintenance_runner()
    if runner is not None:
        return await runner.run_once(force=force, actor=_actor_payload(user))

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
    record_maintenance_event(get_store(), result, actor=_actor_payload(user))
    return result


@router.post("/maintenance/lifecycle")
async def run_lifecycle_cleanup_now(
    user: Annotated[AuthUser, require_role("admin")],
    dry_run: bool = Query(default=True),
) -> dict:
    """Plan or execute conservative lifecycle cleanup for operational data."""
    from storage import get_store
    from web.api import _config

    result = await asyncio.to_thread(
        run_lifecycle_cleanup,
        root=_deployment_root(),
        store=get_store(),
        policy=_lifecycle_policy_from_config(_config),
        archive_mirror_dir=_config.ops.archive_mirror_dir,
        dry_run=dry_run,
    )
    if not dry_run:
        _record_ops_event("lifecycle_cleanup", result, user)
    return result
