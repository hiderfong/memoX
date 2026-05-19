"""Operational check helper tests."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.backup_restore import BackupError
from scripts.ops_check import check_latest_backup, find_latest_backup
from src.ops.readiness import CheckResult, overall_status


def test_overall_status_prefers_errors_then_warnings() -> None:
    assert overall_status([CheckResult("a", "ok", "done")]) == "ok"
    assert overall_status([CheckResult("a", "ok", "done"), CheckResult("b", "warning", "warn")]) == "warning"
    assert overall_status([CheckResult("a", "warning", "warn"), CheckResult("b", "error", "fail")]) == "error"


def test_find_latest_backup_prefers_newest_archive(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    older = backup_dir / "memox-backup-20260519-010101.tar.gz"
    newer = backup_dir / "memox-backup-20260519-020202.tar.gz"
    ignored = backup_dir / "other.tar.gz"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    ignored.write_text("ignored", encoding="utf-8")
    os.utime(older, (100, 100))
    os.utime(newer, (200, 200))
    os.utime(ignored, (300, 300))

    assert find_latest_backup(tmp_path) == newer


def test_latest_backup_warns_when_missing(tmp_path: Path) -> None:
    result = check_latest_backup(tmp_path)

    assert result.status == "warning"
    assert "No backup archive" in result.message


def test_latest_backup_uses_verifier(tmp_path: Path) -> None:
    archive = tmp_path / "backups" / "memox-backup-20260519-010101.tar.gz"
    archive.parent.mkdir()
    archive.write_text("archive", encoding="utf-8")

    def verifier(path: Path) -> dict:
        return {
            "archive": str(path),
            "created_at": "2026-05-19T00:00:00Z",
            "entries": [{"path": "data", "type": "directory"}],
            "verified": True,
        }

    result = check_latest_backup(tmp_path, verifier=verifier)

    assert result.status == "ok"
    assert result.details["entries"] == 1
    assert result.details["verified"] is True


def test_latest_backup_reports_verifier_errors(tmp_path: Path) -> None:
    archive = tmp_path / "backups" / "memox-backup-20260519-010101.tar.gz"
    archive.parent.mkdir()
    archive.write_text("archive", encoding="utf-8")

    def verifier(path: Path) -> dict:
        raise BackupError(f"bad archive: {path.name}")

    result = check_latest_backup(tmp_path, verifier=verifier)

    assert result.status == "error"
    assert "bad archive" in result.message


def test_latest_backup_warns_when_stale_or_too_many(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for index in range(3):
        archive = backup_dir / f"memox-backup-20260519-01010{index}.tar.gz"
        archive.write_text("archive", encoding="utf-8")
        os.utime(archive, (100 + index, 100 + index))

    old_created_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    def verifier(path: Path) -> dict:
        return {
            "archive": str(path),
            "created_at": old_created_at,
            "entries": [{"path": "data", "type": "directory"}],
            "verified": True,
        }

    result = check_latest_backup(tmp_path, verifier=verifier, max_age_hours=1, max_backups=2)

    assert result.status == "warning"
    assert result.details["archive_count"] == 3
    assert result.details["age_seconds"] > 3600
    assert result.details["warnings"] == [
        "latest backup is older than 1h",
        "backup archive count exceeds 2",
    ]
