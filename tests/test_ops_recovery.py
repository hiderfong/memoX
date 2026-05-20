"""Operational recovery drill tests."""

from __future__ import annotations

from pathlib import Path

from src.ops.backup import create_backup
from src.ops.recovery import run_restore_drill


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
