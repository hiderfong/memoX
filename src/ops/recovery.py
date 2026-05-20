"""Operational recovery drills for MemoX backups."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from src.ops.backup import (
    BackupError,
    read_backup_metadata,
    restore_backup,
    target_path,
    validate_metadata_entries,
    verify_backup,
)

CRITICAL_RESTORE_PATHS = ("config.yaml", "data", "workspace")
MAX_PREFLIGHT_ITEMS = 50


def _archive_contains_path(entries: list[Any], rel_path: str) -> bool:
    prefix = f"{rel_path}/"
    return any(entry.path == rel_path or entry.path.startswith(prefix) for entry in entries)


def _critical_path_checks(target: Path, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    included = set(metadata.get("included", []))
    missing = set(metadata.get("missing", []))
    checks: list[dict[str, Any]] = []

    for rel_path in CRITICAL_RESTORE_PATHS:
        restored_path = target / rel_path
        exists = restored_path.exists()
        expected = rel_path in included and rel_path not in missing
        if exists:
            status = "ok"
            message = "Restored"
        elif expected:
            status = "error"
            message = "Expected path was not restored"
        else:
            status = "warning"
            message = "Path was not present in the backup archive"

        checks.append(
            {
                "name": rel_path,
                "status": status,
                "message": message,
                "expected": expected,
                "restored": exists,
            }
        )

    return checks


def run_restore_preflight(archive: str | Path, target: str | Path) -> dict[str, Any]:
    """Verify a backup and report what a restore would overwrite."""
    archive_path = Path(archive).resolve()
    target_root = Path(target).resolve()
    result: dict[str, Any] = {
        "ok": False,
        "status": "error",
        "action": "restore_preflight",
        "archive": str(archive_path),
        "name": archive_path.name,
        "target": str(target_root),
        "writes_performed": False,
        "requires_maintenance_mode": True,
    }

    try:
        metadata = verify_backup(archive_path)
        entries = validate_metadata_entries(metadata)
        existing: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []

        for entry in entries:
            dest = target_path(target_root, entry.path)
            if not dest.exists():
                continue

            existing_item = {
                "path": entry.path,
                "type": entry.type,
                "destination": str(dest),
            }
            existing.append(existing_item)

            reason = ""
            if entry.type == "directory":
                if not dest.is_dir():
                    reason = "type_mismatch"
            elif dest.is_dir():
                reason = "destination_is_directory"
            else:
                reason = "would_overwrite"

            if reason:
                conflicts.append({**existing_item, "reason": reason})

        critical = []
        for rel_path in CRITICAL_RESTORE_PATHS:
            would_overwrite = any(
                conflict["path"] == rel_path or str(conflict["path"]).startswith(f"{rel_path}/")
                for conflict in conflicts
            )
            in_archive = _archive_contains_path(entries, rel_path)
            critical.append(
                {
                    "name": rel_path,
                    "in_archive": in_archive,
                    "target_exists": (target_root / rel_path).exists(),
                    "would_overwrite": would_overwrite,
                    "status": "warning" if would_overwrite or not in_archive else "ok",
                }
            )

        safe_without_overwrite = not conflicts
        status = "ok" if safe_without_overwrite else "warning"
        return {
            **result,
            "ok": True,
            "status": status,
            "message": (
                "Restore preflight passed without overwrite conflicts"
                if safe_without_overwrite
                else "Restore preflight found paths that would be overwritten"
            ),
            "verified": True,
            "safe_without_overwrite": safe_without_overwrite,
            "requires_overwrite": bool(conflicts),
            "created_at": metadata.get("created_at"),
            "included": metadata.get("included", []),
            "missing": metadata.get("missing", []),
            "skipped": metadata.get("skipped", []),
            "entry_count": len(entries),
            "existing_count": len(existing),
            "conflict_count": len(conflicts),
            "existing_preview": existing[:MAX_PREFLIGHT_ITEMS],
            "conflicts": conflicts[:MAX_PREFLIGHT_ITEMS],
            "truncated": len(existing) > MAX_PREFLIGHT_ITEMS or len(conflicts) > MAX_PREFLIGHT_ITEMS,
            "critical_paths": critical,
        }
    except BackupError as exc:
        return {
            **result,
            "message": str(exc),
            "verified": False,
            "safe_without_overwrite": False,
        }
    except Exception as exc:
        return {
            **result,
            "message": f"Restore preflight failed: {type(exc).__name__}: {exc}",
            "verified": False,
            "safe_without_overwrite": False,
        }


def run_restore_drill(archive: str | Path) -> dict[str, Any]:
    """Verify and restore a backup into a disposable directory."""
    archive_path = Path(archive).resolve()
    result: dict[str, Any] = {
        "ok": False,
        "status": "error",
        "action": "restore_drill",
        "archive": str(archive_path),
        "name": archive_path.name,
        "target_removed": True,
    }

    try:
        metadata = read_backup_metadata(archive_path)
        with tempfile.TemporaryDirectory(prefix="memox-restore-drill-") as temp_dir:
            target = Path(temp_dir) / "restore"
            restored = restore_backup(archive=archive_path, target=target)
            checks = _critical_path_checks(target, metadata)
            has_errors = any(check["status"] == "error" for check in checks)
            has_warnings = any(check["status"] == "warning" for check in checks)
            status = "error" if has_errors else "warning" if has_warnings else "ok"
            return {
                **result,
                "ok": not has_errors,
                "status": status,
                "message": "Restore drill completed" if not has_errors else "Restore drill completed with errors",
                "verified": True,
                "created_at": metadata.get("created_at"),
                "included": metadata.get("included", []),
                "missing": metadata.get("missing", []),
                "skipped": metadata.get("skipped", []),
                "entry_count": restored["entry_count"],
                "checks": checks,
            }
    except BackupError as exc:
        return {
            **result,
            "message": str(exc),
            "verified": False,
        }
    except Exception as exc:
        return {
            **result,
            "message": f"Restore drill failed: {type(exc).__name__}: {exc}",
            "verified": False,
        }
