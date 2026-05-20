"""Shared operational readiness checks for CLI and API surfaces."""

from __future__ import annotations

import shutil
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import Config, validate_config
from src.ops.backup import BackupError, list_backup_archives, read_backup_metadata
from src.ops.index_consistency import audit_indexes, build_runtime

Status = str
DEFAULT_MIN_FREE_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_BACKUP_AGE_HOURS = 24.0
DEFAULT_MAX_BACKUP_ARCHIVES = 14


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0


def resolve_from_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (root / candidate).resolve()


def duration_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def overall_status(checks: list[CheckResult]) -> Status:
    statuses = {check.status for check in checks}
    if "error" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    return "ok"


def data_root_from_config(root: Path, config: Config) -> Path:
    return resolve_from_root(root, Path(config.knowledge_base.persist_directory).parent)


def check_config(config_path: Path) -> CheckResult:
    start = time.monotonic()
    if not config_path.exists():
        return CheckResult(
            name="config",
            status="error",
            message=f"Config file not found: {config_path}",
            duration_ms=duration_ms(start),
        )

    try:
        cfg = Config.from_yaml(config_path)
        validate_config(cfg)
    except Exception as exc:
        return CheckResult(
            name="config",
            status="error",
            message=f"Config failed to load or validate: {type(exc).__name__}: {exc}",
            details={"config": str(config_path)},
            duration_ms=duration_ms(start),
        )

    return CheckResult(
        name="config",
        status="ok",
        message=f"Loaded {config_path}",
        details={
            "server": {"host": cfg.server.host, "port": cfg.server.port},
            "workspace": cfg.app.workspace,
            "embedding_provider": cfg.knowledge_base.embedding_provider,
            "auth_enabled": cfg.auth.enabled,
        },
        duration_ms=duration_ms(start),
    )


def check_runtime_config(config: Config, *, label: str) -> CheckResult:
    start = time.monotonic()
    try:
        validate_config(config)
    except Exception as exc:
        return CheckResult(
            name="config",
            status="error",
            message=f"Runtime config failed validation: {type(exc).__name__}: {exc}",
            details={"config": label},
            duration_ms=duration_ms(start),
        )

    return CheckResult(
        name="config",
        status="ok",
        message=f"Runtime config loaded: {label}",
        details={
            "server": {"host": config.server.host, "port": config.server.port},
            "workspace": config.app.workspace,
            "embedding_provider": config.knowledge_base.embedding_provider,
            "auth_enabled": config.auth.enabled,
        },
        duration_ms=duration_ms(start),
    )


def check_persistent_paths(root: Path, config: Config) -> CheckResult:
    start = time.monotonic()
    kb = config.knowledge_base
    hybrid_cfg = kb.hybrid_search or {}
    configured = {
        "workspace": config.app.workspace,
        "chroma": kb.persist_directory,
        "uploads": kb.upload_directory,
        "skills": kb.skills_dir,
        "bm25": hybrid_cfg.get("bm25_persist_path", "./data/bm25_index.pkl"),
        "manifest": kb.manifest_path,
    }
    paths = {name: resolve_from_root(root, value) for name, value in configured.items()}
    directory_names = {"workspace", "chroma", "uploads", "skills"}
    missing_directories = [name for name in sorted(directory_names) if not paths[name].exists()]

    status = "warning" if missing_directories else "ok"
    message = "Persistent directories are present"
    if missing_directories:
        message = "Some persistent directories are missing; this is normal for a fresh deployment"

    return CheckResult(
        name="persistent_paths",
        status=status,
        message=message,
        details={
            "paths": {name: str(path) for name, path in paths.items()},
            "missing_directories": missing_directories,
        },
        duration_ms=duration_ms(start),
    )


def check_index_consistency(
    *,
    root: Path,
    config_path: Path,
    config: Config,
    collection_name: str,
    vector_store: Any | None = None,
    bm25_indexer: Any | None = None,
) -> CheckResult:
    start = time.monotonic()
    try:
        if vector_store is None or bm25_indexer is None:
            vector_store, bm25_indexer, manifest_path = build_runtime(config_path)
        else:
            manifest_path = resolve_from_root(root, config.knowledge_base.manifest_path)
        report = audit_indexes(
            vector_store=vector_store,
            bm25_indexer=bm25_indexer,
            manifest_path=manifest_path,
            collection_name=collection_name,
        )
    except Exception as exc:
        return CheckResult(
            name="index_consistency",
            status="error",
            message=f"Index audit failed: {type(exc).__name__}: {exc}",
            duration_ms=duration_ms(start),
        )

    status = report["status"]
    message = "Chroma, BM25, and manifest are consistent"
    if status == "warning":
        message = "Index audit found warnings"
    elif status == "error":
        message = "Index audit found repairable errors"

    return CheckResult(
        name="index_consistency",
        status=status,
        message=message,
        details={
            "summary": report["summary"],
            "issue_counts": report["issue_counts"],
            "collection": report["collection"],
        },
        duration_ms=duration_ms(start),
    )


def _sqlite_quick_check(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "status": "missing"}
    try:
        uri = f"file:{path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
            version_row = conn.execute("PRAGMA user_version").fetchone()
            schema_version = int(version_row[0]) if version_row else 0
            migration_rows: list[dict[str, Any]] = []
            has_migrations = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if has_migrations:
                migration_rows = [
                    {"version": r[0], "name": r[1], "applied_at": r[2]}
                    for r in conn.execute(
                        "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ]
        result = row[0] if row else "empty"
    except sqlite3.DatabaseError as exc:
        return {"path": str(path), "exists": True, "status": "error", "error": str(exc)}
    return {
        "path": str(path),
        "exists": True,
        "status": "ok" if result == "ok" else "error",
        "quick_check": result,
        "schema_version": schema_version,
        "migrations": migration_rows,
    }


def check_sqlite_databases(root: Path, config: Config) -> CheckResult:
    start = time.monotonic()
    data_root = data_root_from_config(root, config)
    databases = {
        "memox": data_root / "memox.db",
        "workflows": data_root / "workflows.db",
    }
    results = {name: _sqlite_quick_check(path) for name, path in databases.items()}
    if any(item["status"] == "error" for item in results.values()):
        status = "error"
        message = "One or more SQLite databases failed quick_check"
    elif any(item["status"] == "missing" for item in results.values()):
        status = "warning"
        message = "Some SQLite databases are missing; this is normal before first use"
    else:
        status = "ok"
        message = "SQLite databases passed quick_check"
    return CheckResult(
        name="sqlite",
        status=status,
        message=message,
        details={"data_root": str(data_root), "databases": results},
        duration_ms=duration_ms(start),
    )


def _disk_target(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current if current.exists() else Path.cwd()


def check_disk_space(root: Path, config: Config, *, min_free_bytes: int = DEFAULT_MIN_FREE_BYTES) -> CheckResult:
    start = time.monotonic()
    data_root = data_root_from_config(root, config)
    target = _disk_target(data_root)
    usage = shutil.disk_usage(target)
    status = "warning" if usage.free < min_free_bytes else "ok"
    message = "Disk space is above warning threshold"
    if status == "warning":
        message = "Disk free space is below warning threshold"
    return CheckResult(
        name="disk",
        status=status,
        message=message,
        details={
            "path": str(target),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "min_free_bytes": min_free_bytes,
        },
        duration_ms=duration_ms(start),
    )


def _parse_backup_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def check_latest_backup(
    root: Path,
    *,
    max_age_hours: float = DEFAULT_MAX_BACKUP_AGE_HOURS,
    max_backups: int = DEFAULT_MAX_BACKUP_ARCHIVES,
) -> CheckResult:
    start = time.monotonic()
    backup_dir = root.resolve() / "backups"
    archives = list_backup_archives(root)
    if not archives:
        return CheckResult(
            name="latest_backup",
            status="warning",
            message="No backup archive found under backups/",
            details={
                "backup_dir": str(backup_dir),
                "archive_count": 0,
                "max_age_hours": max_age_hours,
                "max_backups": max_backups,
                "warnings": ["no backup archive found"],
            },
            duration_ms=duration_ms(start),
        )

    backup_path = archives[0]
    try:
        metadata = read_backup_metadata(backup_path)
    except BackupError as exc:
        return CheckResult(
            name="latest_backup",
            status="error",
            message=f"Backup metadata inspection failed: {type(exc).__name__}: {exc}",
            details={
                "backup_dir": str(backup_dir),
                "archive": str(backup_path),
                "archive_count": len(archives),
            },
            duration_ms=duration_ms(start),
        )

    created_at = _parse_backup_timestamp(metadata.get("created_at"))
    age_seconds = None
    warnings: list[str] = []
    if created_at is None:
        warnings.append("created_at is missing or invalid")
    else:
        age_seconds = max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())
        if age_seconds > max_age_hours * 3600:
            warnings.append(f"latest backup is older than {max_age_hours:g}h")
    if len(archives) > max_backups:
        warnings.append(f"backup archive count exceeds {max_backups}")

    status = "warning" if warnings else "ok"
    message = f"Latest backup metadata inspected: {backup_path}"
    if warnings:
        message += " (" + "; ".join(warnings) + ")"

    return CheckResult(
        name="latest_backup",
        status=status,
        message=message,
        details={
            "backup_dir": str(backup_dir),
            "archive": str(backup_path),
            "created_at": metadata.get("created_at"),
            "age_seconds": age_seconds,
            "archive_count": len(archives),
            "max_age_hours": max_age_hours,
            "max_backups": max_backups,
            "entries": len(metadata.get("entries", [])),
            "verified": False,
            "warnings": warnings,
        },
        duration_ms=duration_ms(start),
    )


def run_readiness_checks(
    *,
    root: Path,
    config_path: Path,
    config: Config | None = None,
    collection_name: str = "documents",
    vector_store: Any | None = None,
    bm25_indexer: Any | None = None,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    include_backup: bool = False,
    max_backup_age_hours: float = DEFAULT_MAX_BACKUP_AGE_HOURS,
    max_backups: int = DEFAULT_MAX_BACKUP_ARCHIVES,
) -> dict[str, Any]:
    if config is None:
        checks: list[CheckResult] = [check_config(config_path)]
        try:
            config = Config.from_yaml(config_path)
        except Exception:
            status = overall_status(checks)
            return {
                "ok": status != "error",
                "status": status,
                "root": str(root),
                "config": str(config_path),
                "checks": [asdict(check) for check in checks],
            }
    else:
        checks = [check_runtime_config(config, label=str(config_path))]
        if checks[0].status == "error":
            status = overall_status(checks)
            return {
                "ok": status != "error",
                "status": status,
                "root": str(root),
                "config": str(config_path),
                "checks": [asdict(check) for check in checks],
            }

    assert config is not None
    checks.extend(
        [
            check_persistent_paths(root, config),
            check_index_consistency(
                root=root,
                config_path=config_path,
                config=config,
                collection_name=collection_name,
                vector_store=vector_store,
                bm25_indexer=bm25_indexer,
            ),
            check_sqlite_databases(root, config),
            check_disk_space(root, config, min_free_bytes=min_free_bytes),
        ]
    )
    if include_backup:
        checks.append(
            check_latest_backup(
                root,
                max_age_hours=max_backup_age_hours,
                max_backups=max_backups,
            )
        )

    status = overall_status(checks)
    return {
        "ok": status != "error",
        "status": status,
        "root": str(root),
        "config": str(config_path),
        "checks": [asdict(check) for check in checks],
    }
