"""Operational recovery drill tests."""

from __future__ import annotations

from pathlib import Path

from src.ops.backup import create_backup, restore_backup
from src.ops.recovery import run_restore_drill, run_restore_execute, run_restore_preflight


def _write_runtime_paths(root: Path, marker: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(f"app:\n  name: {marker}\n", encoding="utf-8")
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "memo.txt").write_text(f"{marker} memo\n", encoding="utf-8")
    (root / "workspace").mkdir(exist_ok=True)
    (root / "workspace" / "artifact.txt").write_text(f"{marker} artifact\n", encoding="utf-8")


def test_restore_preflight_passes_for_empty_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "config.yaml").write_text("app:\n  name: MemoX\n", encoding="utf-8")
    (source / "data").mkdir()
    (source / "data" / "memo.txt").write_text("memo\n", encoding="utf-8")
    (source / "workspace").mkdir()
    (source / "workspace" / "artifact.txt").write_text("artifact\n", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    create_backup(root=source, output=archive, include=("config.yaml", "data", "workspace"))

    result = run_restore_preflight(archive, target)

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["verified"] is True
    assert result["safe_without_overwrite"] is True
    assert result["conflict_count"] == 0
    assert result["writes_performed"] is False


def test_restore_execute_restores_after_safety_backup(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "deployment"
    _write_runtime_paths(source, "restored")
    _write_runtime_paths(target, "current")
    archive = tmp_path / "backup.tar.gz"
    create_backup(root=source, output=archive, include=("config.yaml", "data", "workspace"))

    result = run_restore_execute(
        archive,
        target,
        confirm_archive_name=archive.name,
        acknowledge_overwrite=True,
        acknowledge_maintenance_mode=True,
        safety_include=("config.yaml", "data", "workspace"),
    )

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["action"] == "restored"
    assert result["writes_performed"] is True
    assert result["safety_backup_created"] is True
    safety_archive = Path(result["safety_backup"]["archive"])
    assert safety_archive.exists()
    assert (target / "config.yaml").read_text(encoding="utf-8") == "app:\n  name: restored\n"
    assert (target / "data" / "memo.txt").read_text(encoding="utf-8") == "restored memo\n"
    assert (target / "workspace" / "artifact.txt").read_text(encoding="utf-8") == "restored artifact\n"

    safety_restore = tmp_path / "safety-restore"
    restore_backup(archive=safety_archive, target=safety_restore)
    assert (safety_restore / "config.yaml").read_text(encoding="utf-8") == "app:\n  name: current\n"
    assert (safety_restore / "data" / "memo.txt").read_text(encoding="utf-8") == "current memo\n"


def test_restore_execute_rejects_without_exact_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "deployment"
    _write_runtime_paths(source, "restored")
    _write_runtime_paths(target, "current")
    archive = tmp_path / "backup.tar.gz"
    create_backup(root=source, output=archive, include=("config.yaml", "data", "workspace"))

    result = run_restore_execute(
        archive,
        target,
        confirm_archive_name="wrong.tar.gz",
        acknowledge_overwrite=True,
        acknowledge_maintenance_mode=True,
        safety_include=("config.yaml", "data", "workspace"),
    )

    assert result["ok"] is False
    assert result["status"] == "warning"
    assert result["action"] == "rejected"
    assert result["writes_performed"] is False
    assert not (target / "backups").exists()
    assert (target / "data" / "memo.txt").read_text(encoding="utf-8") == "current memo\n"


def test_restore_execute_rejects_without_overwrite_acknowledgement(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "deployment"
    _write_runtime_paths(source, "restored")
    _write_runtime_paths(target, "current")
    archive = tmp_path / "backup.tar.gz"
    create_backup(root=source, output=archive, include=("config.yaml", "data", "workspace"))

    result = run_restore_execute(
        archive,
        target,
        confirm_archive_name=archive.name,
        acknowledge_overwrite=False,
        acknowledge_maintenance_mode=True,
        safety_include=("config.yaml", "data", "workspace"),
    )

    assert result["ok"] is False
    assert result["status"] == "warning"
    assert result["action"] == "rejected"
    assert result["preflight"]["requires_overwrite"] is True
    assert result["writes_performed"] is False
    assert not (target / "backups").exists()
    assert (target / "workspace" / "artifact.txt").read_text(encoding="utf-8") == "current artifact\n"


def test_restore_preflight_reports_current_deployment_overwrites(tmp_path: Path) -> None:
    root = tmp_path / "deployment"
    root.mkdir()
    (root / "config.yaml").write_text("app:\n  name: MemoX\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "memo.txt").write_text("memo\n", encoding="utf-8")
    (root / "workspace").mkdir()
    (root / "workspace" / "artifact.txt").write_text("artifact\n", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    create_backup(root=root, output=archive, include=("config.yaml", "data", "workspace"))

    result = run_restore_preflight(archive, root)

    assert result["ok"] is True
    assert result["status"] == "warning"
    assert result["safe_without_overwrite"] is False
    assert result["requires_overwrite"] is True
    assert result["conflict_count"] >= 3
    conflict_paths = {item["path"] for item in result["conflicts"]}
    assert {"config.yaml", "data/memo.txt", "workspace/artifact.txt"}.issubset(conflict_paths)
    critical = {item["name"]: item for item in result["critical_paths"]}
    assert critical["config.yaml"]["would_overwrite"] is True
    assert critical["data"]["would_overwrite"] is True
    assert critical["workspace"]["would_overwrite"] is True


def test_restore_drill_restores_critical_paths(tmp_path: Path) -> None:
    root = tmp_path / "deployment"
    root.mkdir()
    (root / "config.yaml").write_text("app:\n  name: MemoX\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "memo.txt").write_text("memo\n", encoding="utf-8")
    (root / "workspace").mkdir()
    (root / "workspace" / "artifact.txt").write_text("artifact\n", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    create_backup(root=root, output=archive, include=("config.yaml", "data", "workspace"))

    result = run_restore_drill(archive)

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["verified"] is True
    assert result["entry_count"] > 0
    assert {check["name"]: check["status"] for check in result["checks"]} == {
        "config.yaml": "ok",
        "data": "ok",
        "workspace": "ok",
    }


def test_restore_drill_warns_when_runtime_paths_were_missing(tmp_path: Path) -> None:
    root = tmp_path / "deployment"
    root.mkdir()
    (root / "data").mkdir()
    archive = tmp_path / "backup.tar.gz"
    create_backup(root=root, output=archive, include=("config.yaml", "data", "workspace"))

    result = run_restore_drill(archive)

    assert result["ok"] is True
    assert result["status"] == "warning"
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["data"]["status"] == "ok"
    assert checks["config.yaml"]["status"] == "warning"
    assert checks["workspace"]["status"] == "warning"
