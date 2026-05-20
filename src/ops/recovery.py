"""Operational recovery drills for MemoX backups."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from src.ops.backup import BackupError, read_backup_metadata, restore_backup

CRITICAL_RESTORE_PATHS = ("config.yaml", "data", "workspace")


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
