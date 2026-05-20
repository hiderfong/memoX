"""Conservative lifecycle cleanup for non-core operational data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LifecyclePolicy:
    ops_event_retention_days: int = 90
    audit_log_retention_days: int = 180
    diagnostic_retention_days: int = 30
    max_diagnostic_bundles: int = 20


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cutoff_iso(days: int, *, now: datetime) -> str | None:
    if days <= 0:
        return None
    return (now - timedelta(days=days)).isoformat()


def _resolve_mirror_root(root: Path, mirror_dir: str | Path | None) -> Path | None:
    if mirror_dir is None:
        return None
    value = str(mirror_dir).strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _diagnostic_dirs(root: Path, mirror_dir: str | Path | None) -> list[Path]:
    dirs = [root / "diagnostics"]
    mirror_root = _resolve_mirror_root(root, mirror_dir)
    if mirror_root is not None:
        mirror_diagnostics = mirror_root / "diagnostics"
        if mirror_diagnostics.resolve() not in {item.resolve() for item in dirs}:
            dirs.append(mirror_diagnostics)
    return dirs


def _diagnostic_candidates(root: Path, mirror_dir: str | Path | None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for directory in _diagnostic_dirs(root, mirror_dir):
        if not directory.exists():
            continue
        for path in directory.glob("memox-diagnostics-*.zip"):
            if not path.is_file():
                continue
            stat = path.stat()
            candidates.append({
                "path": str(path.resolve()),
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(tzinfo=None).isoformat(),
                "modified_ts": stat.st_mtime,
            })
    return sorted(candidates, key=lambda item: item["modified_ts"], reverse=True)


def _select_diagnostic_deletions(
    candidates: list[dict[str, Any]],
    *,
    retention_days: int,
    max_bundles: int,
    now: datetime,
) -> list[dict[str, Any]]:
    cutoff_ts = None
    if retention_days > 0:
        cutoff_ts = (now - timedelta(days=retention_days)).timestamp()
    selected: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(candidates):
        too_old = cutoff_ts is not None and item["modified_ts"] < cutoff_ts
        over_count = index >= max_bundles
        if too_old or over_count:
            selected[item["path"]] = {
                key: value for key, value in item.items() if key != "modified_ts"
            } | {
                "reason": "age" if too_old else "count",
            }
    return list(selected.values())


def _table_plan(
    *,
    name: str,
    retention_days: int,
    cutoff_iso: str | None,
    eligible_count: int,
) -> dict[str, Any]:
    return {
        "name": name,
        "retention_days": retention_days,
        "cutoff": cutoff_iso or "",
        "eligible_count": eligible_count,
        "deleted_count": 0,
    }


def run_lifecycle_cleanup(
    *,
    root: str | Path,
    store: Any | None,
    policy: LifecyclePolicy,
    archive_mirror_dir: str | Path | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Plan or execute cleanup of non-core operational data."""
    root_path = Path(root).resolve()
    now = _utc_now()
    ops_cutoff = _cutoff_iso(policy.ops_event_retention_days, now=now)
    audit_cutoff = _cutoff_iso(policy.audit_log_retention_days, now=now)

    tables: list[dict[str, Any]] = []
    if store is not None:
        ops_count = store.count_ops_events_before(ops_cutoff) if ops_cutoff else 0
        audit_count = store.count_audit_events_before(audit_cutoff) if audit_cutoff else 0
    else:
        ops_count = 0
        audit_count = 0

    tables.append(
        _table_plan(
            name="ops_events",
            retention_days=policy.ops_event_retention_days,
            cutoff_iso=ops_cutoff,
            eligible_count=ops_count,
        )
    )
    tables.append(
        _table_plan(
            name="audit_log",
            retention_days=policy.audit_log_retention_days,
            cutoff_iso=audit_cutoff,
            eligible_count=audit_count,
        )
    )

    diagnostics = _diagnostic_candidates(root_path, archive_mirror_dir)
    diagnostic_deletions = _select_diagnostic_deletions(
        diagnostics,
        retention_days=policy.diagnostic_retention_days,
        max_bundles=policy.max_diagnostic_bundles,
        now=now,
    )

    if not dry_run and store is not None:
        if ops_cutoff:
            tables[0]["deleted_count"] = store.delete_ops_events_before(ops_cutoff)
        if audit_cutoff:
            tables[1]["deleted_count"] = store.delete_audit_events_before(audit_cutoff)

    deleted_diagnostics: list[dict[str, Any]] = []
    if not dry_run:
        for item in diagnostic_deletions:
            path = Path(item["path"])
            try:
                path.unlink()
                deleted_diagnostics.append(item)
            except FileNotFoundError:
                deleted_diagnostics.append({**item, "missing": True})

    eligible_records = sum(table["eligible_count"] for table in tables)
    deleted_records = sum(table["deleted_count"] for table in tables)
    diagnostic_bytes = sum(item["size_bytes"] for item in diagnostic_deletions)
    result = {
        "ok": True,
        "status": "ok",
        "action": "dry_run" if dry_run else "executed",
        "message": "Lifecycle cleanup dry-run completed" if dry_run else "Lifecycle cleanup executed",
        "dry_run": dry_run,
        "root": str(root_path),
        "policy": {
            "ops_event_retention_days": policy.ops_event_retention_days,
            "audit_log_retention_days": policy.audit_log_retention_days,
            "diagnostic_retention_days": policy.diagnostic_retention_days,
            "max_diagnostic_bundles": policy.max_diagnostic_bundles,
        },
        "tables": tables,
        "diagnostics": {
            "candidate_count": len(diagnostics),
            "eligible_count": len(diagnostic_deletions),
            "deleted_count": len(deleted_diagnostics),
            "eligible_bytes": diagnostic_bytes,
            "eligible": diagnostic_deletions,
            "deleted": deleted_diagnostics,
        },
        "summary": {
            "eligible_records": eligible_records,
            "deleted_records": deleted_records,
            "eligible_files": len(diagnostic_deletions),
            "deleted_files": len(deleted_diagnostics),
            "eligible_bytes": diagnostic_bytes,
            "core_user_data_deleted": False,
        },
    }
    if store is None:
        result["status"] = "warning"
        result["message"] = f"{result['message']}; persistence store is unavailable"
    return result
