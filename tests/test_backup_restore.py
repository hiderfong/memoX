"""Deployment backup and restore tests."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts.backup_restore import (
    BackupError,
    create_backup,
    list_backup_archives,
    prune_backups,
    read_backup_metadata,
    restore_backup,
    verify_backup,
)

ROOT = Path(__file__).parents[1]


def _write_sample_deployment(root: Path) -> None:
    (root / "config.yaml").write_text("app:\n  name: MemoX\n", encoding="utf-8")
    (root / ".env").write_text("MEMOX_ADMIN_PASSWORD=secret\n", encoding="utf-8")
    (root / "data" / "uploads").mkdir(parents=True)
    (root / "data" / "uploads" / "note.md").write_text("# MemoX\npersistent note\n", encoding="utf-8")
    (root / "data" / "chroma").mkdir()
    (root / "workspace" / "task-1" / "shared").mkdir(parents=True)
    (root / "workspace" / "task-1" / "shared" / "result.txt").write_text("done", encoding="utf-8")


def test_backup_restore_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "restore"
    source.mkdir()
    _write_sample_deployment(source)

    archive = tmp_path / "backup.tar.gz"
    created = create_backup(root=source, output=archive)
    verified = verify_backup(archive)

    assert created["archive"] == str(archive.resolve())
    assert verified["verified"] is True
    assert any(entry["path"] == "data/uploads/note.md" for entry in verified["entries"])
    assert any(entry["path"] == "workspace/task-1/shared/result.txt" for entry in verified["entries"])

    restored = restore_backup(archive=archive, target=target)

    assert restored["entry_count"] == len(verified["entries"])
    assert (target / "config.yaml").read_text(encoding="utf-8") == "app:\n  name: MemoX\n"
    assert (target / ".env").read_text(encoding="utf-8") == "MEMOX_ADMIN_PASSWORD=secret\n"
    assert (target / "data" / "uploads" / "note.md").read_text(encoding="utf-8") == "# MemoX\npersistent note\n"
    assert (target / "data" / "chroma").is_dir()
    assert (target / "workspace" / "task-1" / "shared" / "result.txt").read_text(encoding="utf-8") == "done"


def test_restore_refuses_existing_file_without_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "restore"
    source.mkdir()
    target.mkdir()
    _write_sample_deployment(source)
    (target / "config.yaml").write_text("existing", encoding="utf-8")

    archive = tmp_path / "backup.tar.gz"
    create_backup(root=source, output=archive)

    with pytest.raises(BackupError, match="overwrite existing paths"):
        restore_backup(archive=archive, target=target)

    restored = restore_backup(archive=archive, target=target, overwrite=True)
    assert restored["entry_count"] > 0
    assert (target / "config.yaml").read_text(encoding="utf-8") == "app:\n  name: MemoX\n"


def test_backup_records_missing_optional_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").mkdir()

    archive = tmp_path / "backup.tar.gz"
    metadata = create_backup(root=source, output=archive)

    assert "config.yaml" in metadata["missing"]
    assert ".env" in metadata["missing"]
    assert "workspace" in metadata["missing"]
    assert read_backup_metadata(archive)["format"] == "memox-backup-v1"


def test_prune_backups_keeps_newest_archives(tmp_path: Path) -> None:
    root = tmp_path / "deployment"
    backup_dir = root / "backups"
    backup_dir.mkdir(parents=True)
    archives = [
        backup_dir / "memox-backup-20260519-010101.tar.gz",
        backup_dir / "memox-backup-20260519-020202.tar.gz",
        backup_dir / "memox-backup-20260519-030303.tar.gz",
    ]
    for index, archive in enumerate(archives, start=1):
        archive.write_text(str(index), encoding="utf-8")
        os.utime(archive, (index, index))

    dry_run = prune_backups(root=root, keep=2, dry_run=True)

    assert dry_run["archive_count_after"] == 3
    assert all(archive.exists() for archive in archives)

    result = prune_backups(root=root, keep=2)

    assert result["archive_count_before"] == 3
    assert result["archive_count_after"] == 2
    assert [path.name for path in list_backup_archives(root)] == [
        "memox-backup-20260519-030303.tar.gz",
        "memox-backup-20260519-020202.tar.gz",
    ]
    assert not archives[0].exists()


def test_verify_rejects_unsafe_metadata_path(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    metadata = {
        "format": "memox-backup-v1",
        "created_at": "2026-05-19T00:00:00Z",
        "entries": [{"path": "../evil", "type": "file", "size": 0, "sha256": "0" * 64}],
    }

    with tarfile.open(archive, "w:gz") as tar:
        data = json.dumps(metadata).encode("utf-8")
        info = tarfile.TarInfo("memox-backup.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    with pytest.raises(BackupError, match="Unsafe archive path"):
        verify_backup(archive)


def test_cli_create_verify_restore_json(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "restore"
    source.mkdir()
    _write_sample_deployment(source)
    archive = tmp_path / "cli-backup.tar.gz"
    script = ROOT / "scripts" / "backup_restore.py"

    created = subprocess.run(
        [sys.executable, str(script), "create", "--root", str(source), "--output", str(archive), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    created_payload = json.loads(created.stdout)
    assert created_payload["archive"] == str(archive.resolve())

    verified = subprocess.run(
        [sys.executable, str(script), "verify", str(archive), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(verified.stdout)["verified"] is True

    subprocess.run(
        [sys.executable, str(script), "restore", str(archive), "--target", str(target), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (target / "data" / "uploads" / "note.md").read_text(encoding="utf-8") == "# MemoX\npersistent note\n"
